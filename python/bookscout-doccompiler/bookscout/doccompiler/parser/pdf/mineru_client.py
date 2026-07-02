"""MinerU cloud API client (async, httpx-based).

Implements the batch upload flow from the MinerU 精准解析 API:
1. POST ``/api/v4/file-urls/batch`` → obtain pre-signed upload URLs + batch_id.
2. PUT the file bytes to the upload URL.
3. Poll ``/api/v4/extract-results/batch/{batch_id}`` until state == ``done``.
4. Download the result ZIP from ``full_zip_url``.

API docs: https://mineru.net/apiManage/docs
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import pathlib
import typing as t

import httpx

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

MINERU_BASE_URL = "https://mineru.net"
DEFAULT_POLL_INTERVAL = 5.0
DEFAULT_POLL_TIMEOUT = 600.0


@dataclasses.dataclass(frozen=True, slots=True)
class MineruBatchResult:
    """Result of one MinerU batch parse.

    Attributes:
        file_name: Original file name as registered with MinerU.
        zip_bytes: Raw bytes of the result ZIP archive.
        zip_url: CDN URL the ZIP was downloaded from.
    """

    file_name: str
    zip_bytes: bytes
    zip_url: str


@dataclasses.dataclass(frozen=True, slots=True)
class MineruTaskProgress:
    """Progress snapshot for a running MinerU task.

    Attributes:
        state: Task state (``pending``, ``running``, ``done``, ``failed``, ...).
        extracted_pages: Pages processed so far (when running).
        total_pages: Total pages in the document (when running).
        err_msg: Error message when ``state == "failed"``.
    """

    state: str
    extracted_pages: int
    total_pages: int
    err_msg: str


class MineruClient(LoggingMixin, AsyncResourceMixin):
    """Async client for the MinerU 精准解析 API.

    Args:
        logger: Logger instance.
        token: MinerU API token. If ``None``, reads ``MINERU_API_TOKEN`` env.
        base_url: MinerU API base URL.
        poll_interval: Seconds between polling attempts.
        poll_timeout: Maximum seconds to wait for a task.
    """

    def __init__(
        self,
        logger: Logger,
        token: str | None = None,
        base_url: str = MINERU_BASE_URL,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT,
    ) -> None:
        super().__init__(logger=logger)
        self._token = token or os.environ.get("MINERU_API_TOKEN", "")
        if not self._token:
            raise ValueError("MINERU_API_TOKEN is required (set env or pass token=)")
        self._base_url = base_url.rstrip("/")
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._client: httpx.AsyncClient | None = None

    async def startup(self) -> None:
        """Create the underlying httpx async client."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(300.0, connect=30.0),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        await super().startup()
        self.logger.info("mineru client started", base_url=self._base_url)

    async def shutdown(self) -> None:
        """Close the httpx client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def submit_and_wait(
        self,
        file_path: pathlib.Path,
        data_id: str,
        page_ranges: str | None = None,
        model_version: str = "vlm",
    ) -> MineruBatchResult:
        """Submit a local file for parsing and wait for the result ZIP.

        Uses the batch upload flow:
        1. Request upload URL(s).
        2. PUT the file to the pre-signed URL.
        3. Poll until done.
        4. Download the result ZIP.

        Args:
            file_path: Path to the local PDF file.
            data_id: A unique data_id for this submission.
            page_ranges: Optional page range string (e.g. ``"1-100"``).
            model_version: ``"vlm"`` or ``"pipeline"``.

        Returns:
            A :class:`MineruBatchResult` with the ZIP bytes.

        Raises:
            RuntimeError: If the task fails or polling times out.
        """
        file_name = file_path.name
        self.logger.info("submitting file to mineru", file_name=file_name, data_id=data_id)

        # Step 1: request upload URL.
        batch_id, upload_url = await self._request_upload_url(
            file_name=file_name,
            data_id=data_id,
            page_ranges=page_ranges,
            model_version=model_version,
        )
        self.logger.info("upload url obtained", batch_id=batch_id, file_name=file_name)

        # Step 2: upload file.
        await self._upload_file(file_path, upload_url)
        self.logger.info("file uploaded to mineru", file_name=file_name)

        # Step 3: poll for result.
        result = await self._poll_batch(batch_id, file_name)
        self.logger.info("mineru task done", file_name=file_name, zip_url=result.zip_url)

        # Step 4: download ZIP.
        zip_bytes = await self._download_zip(result.zip_url)
        self.logger.info("zip downloaded", file_name=file_name, size=len(zip_bytes))
        return MineruBatchResult(
            file_name=file_name,
            zip_bytes=zip_bytes,
            zip_url=result.zip_url,
        )

    async def _request_upload_url(
        self,
        file_name: str,
        data_id: str,
        page_ranges: str | None,
        model_version: str,
    ) -> tuple[str, str]:
        """Request a pre-signed upload URL from MinerU."""
        assert self._client is not None
        file_entry: dict[str, t.Any] = {"name": file_name, "data_id": data_id}
        if page_ranges:
            file_entry["page_ranges"] = page_ranges
        payload = {"files": [file_entry], "model_version": model_version}

        resp = await self._client.post("/api/v4/file-urls/batch", json=payload)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"MinerU upload URL request failed: {data.get('msg', data)}")
        batch_id = data["data"]["batch_id"]
        file_urls = data["data"]["file_urls"]
        if not file_urls:
            raise RuntimeError("MinerU returned no upload URLs")
        return batch_id, file_urls[0]

    async def _upload_file(self, file_path: pathlib.Path, upload_url: str) -> None:
        """PUT the file bytes to the pre-signed URL (no auth header)."""
        file_bytes = file_path.read_bytes()
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as raw_client:
            resp = await raw_client.put(upload_url, content=file_bytes)
            resp.raise_for_status()

    async def _poll_batch(self, batch_id: str, file_name: str) -> MineruBatchResult:
        """Poll the batch endpoint until the file's state is ``done`` or ``failed``."""
        assert self._client is not None
        url = f"/api/v4/extract-results/batch/{batch_id}"
        elapsed = 0.0

        while elapsed < self._poll_timeout:
            resp = await self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"MinerU poll failed: {data.get('msg', data)}")

            results = data["data"].get("extract_result", [])
            entry = next((r for r in results if r.get("file_name") == file_name), None)
            if entry is None:
                self.logger.debug("waiting for file to appear in batch", batch_id=batch_id)
                await asyncio.sleep(self._poll_interval)
                elapsed += self._poll_interval
                continue

            state = entry.get("state", "")
            progress = self._extract_progress(entry, state)
            self.logger.info(
                "mineru poll",
                file_name=file_name,
                state=state,
                pages=f"{progress.extracted_pages}/{progress.total_pages}",
            )

            if state == "done":
                zip_url = entry.get("full_zip_url", "")
                if not zip_url:
                    raise RuntimeError(f"MinerU task done but no zip URL: {entry}")
                return MineruBatchResult(
                    file_name=file_name,
                    zip_bytes=b"",
                    zip_url=zip_url,
                )
            if state == "failed":
                err = entry.get("err_msg", "unknown error")
                raise RuntimeError(f"MinerU task failed: {err}")

            await asyncio.sleep(self._poll_interval)
            elapsed += self._poll_interval

        raise TimeoutError(f"MinerU polling timed out after {self._poll_timeout}s for {file_name}")

    @staticmethod
    def _extract_progress(entry: dict[str, t.Any], state: str) -> MineruTaskProgress:
        """Extract progress info from a poll response entry."""
        prog = entry.get("extract_progress", {})
        return MineruTaskProgress(
            state=state,
            extracted_pages=int(prog.get("extracted_pages", 0)),
            total_pages=int(prog.get("total_pages", 0)),
            err_msg=entry.get("err_msg", ""),
        )

    async def _download_zip(self, zip_url: str) -> bytes:
        """Download the result ZIP from a CDN URL."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as raw_client:
            resp = await raw_client.get(zip_url)
            resp.raise_for_status()
            return resp.content
