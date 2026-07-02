"""MinerU API PDF parser (spec §5.6, §5.7).

Uses :class:`~bookscout.doccompiler.parser.pdf.mineru_client.MineruClient` to
submit PDFs to the MinerU cloud API, downloads result ZIPs, and merges
per-batch markdown + span data into a unified ``CONTENT.md`` with PDF source
mappings (page index + bbox).

Supports PDF batch splitting when page count exceeds the API limit.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import typing as t
import zipfile

import aiofiles
from pypdf import PdfReader
from pypdf import PdfWriter

from bookscout.doccompiler.parser.pdf import PdfParser
from bookscout.doccompiler.parser.pdf.mapping_store import PdfMappingStore
from bookscout.doccompiler.parser.pdf.mineru_client import MineruClient
from bookscout.doccompiler.types import ParserResult
from bookscout.doccompiler.types import PdfSourceMapping
from bookscout.doccompiler.types import SourceInfo
from bookscout.doccompiler.workspace import BookWorkspace

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

PARSER_NAME = "MineruPdfParser"
PARSER_VERSION = "0.1.0"

# API max is 200 pages; use 0.95 safety margin → 190.
DEFAULT_MAX_PAGES_PER_BATCH = 190


class MineruPdfParser(PdfParser):
    """PDF parser backed by the MinerU cloud API.

    Args:
        logger: Logger instance.
        max_pages_per_batch: Maximum pages per MinerU submission batch.
        model_version: MinerU model (``"vlm"`` or ``"pipeline"``).
        client: Optional pre-configured :class:`MineruClient`.
    """

    def __init__(
        self,
        logger: Logger,
        max_pages_per_batch: int = DEFAULT_MAX_PAGES_PER_BATCH,
        model_version: str = "vlm",
        client: MineruClient | None = None,
    ) -> None:
        super().__init__(logger)
        self._max_pages = max_pages_per_batch
        self._model_version = model_version
        self._client = client

    async def startup(self) -> None:
        """Start the MinerU client (creating one if not provided)."""
        if self._client is None:
            self._client = MineruClient(logger=self.logger)
        await self._client.startup()
        await super().startup()

    async def shutdown(self) -> None:
        """Shut down the MinerU client."""
        if self._client is not None:
            await self._client.shutdown()
        await super().shutdown()

    async def parse(
        self,
        source_path: pathlib.Path,
        book_id: str,
        workspace: BookWorkspace,
    ) -> ParserResult:
        """Parse a PDF via MinerU into CONTENT.md + PDF mapping SQLite.

        Args:
            source_path: Path to the ``.pdf`` file.
            book_id: The book id.
            workspace: The book workspace for output artifacts.

        Returns:
            A :class:`ParserResult`.
        """
        self.logger.info("pdf parse starting", source=str(source_path), book_id=book_id)

        # Copy source into workspace.
        source_copy = workspace.source_file_path("original.pdf")
        async with aiofiles.open(source_path, "rb") as src, aiofiles.open(source_copy, "wb") as dst:
            await dst.write(await src.read())

        checksum = await self._sha256(source_path)
        file_size = source_path.stat().st_size
        source_info = SourceInfo(
            book_id=book_id,
            source_type="pdf",
            file_path=str(source_copy),
            file_size=file_size,
            checksum=checksum,
        )
        self.logger.info("source info", checksum=checksum, size=file_size)

        # Determine page count and batches.
        total_pages = self._count_pages(source_path)
        batches = self._plan_batches(total_pages)
        self.logger.info(
            "pdf batch plan",
            total_pages=total_pages,
            batches=len(batches),
            batch_ranges=[f"{s + 1}-{e}" for s, e in batches],
        )

        # Process each batch.
        batch_results: list[_BatchResult] = []
        for batch_idx, (page_start, page_end) in enumerate(batches):
            self.logger.info(
                "processing batch",
                batch_idx=batch_idx,
                pages=f"{page_start + 1}-{page_end}",
            )
            result = await self._process_batch(
                source_path=source_path,
                book_id=book_id,
                workspace=workspace,
                batch_idx=batch_idx,
                page_start=page_start,
                page_end=page_end,
                total_batches=len(batches),
            )
            batch_results.append(result)

        # Merge batch results.
        content, mappings = self._merge_batches(book_id, batch_results)
        self.logger.info("batches merged", content_chars=len(content), mappings=len(mappings))

        # Save merged artifacts.
        merged_dir = workspace.mineru_merged_dir
        merged_md = merged_dir / "full.md"
        async with aiofiles.open(merged_md, "w", encoding="utf-8") as f:
            await f.write(content)
        merged_json = merged_dir / "spans_merged.json"
        async with aiofiles.open(merged_json, "w", encoding="utf-8") as f:
            await f.write(
                json.dumps(
                    [
                        {
                            "content_offset": m.content_offset,
                            "content_length": m.content_length,
                            "page_index": m.page_index,
                            "bbox": [m.x0, m.y0, m.x1, m.y1],
                        }
                        for m in mappings
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )

        # Write CONTENT.md.
        content_path = workspace.content_path
        async with aiofiles.open(content_path, "w", encoding="utf-8") as f:
            await f.write(content)
        self.logger.info("CONTENT.md written", path=str(content_path), chars=len(content))

        # Write mapping SQLite.
        mapping_db = workspace.mapping_db_path("pdf")
        store = PdfMappingStore(logger=self.logger, db_path=mapping_db)
        await store.startup()
        try:
            await store.write_mappings(mappings)
        finally:
            await store.shutdown()
        self.logger.info("pdf mapping db written", path=str(mapping_db), mappings=len(mappings))

        return ParserResult(
            book_id=book_id,
            source_info=source_info,
            content_path=str(content_path),
            mapping_db_path=str(mapping_db),
            metadata={"parser": PARSER_NAME, "model_version": self._model_version},
        )

    @staticmethod
    def _count_pages(path: pathlib.Path) -> int:
        """Count PDF pages using pypdf."""
        reader = PdfReader(str(path))
        return len(reader.pages)

    def _plan_batches(self, total_pages: int) -> list[tuple[int, int]]:
        """Split total pages into (start, end) 0-based half-open ranges.

        Args:
            total_pages: Total page count.

        Returns:
            List of ``(page_start, page_end)`` tuples (0-based, end exclusive).
        """
        if total_pages <= self._max_pages:
            return [(0, total_pages)]
        batches: list[tuple[int, int]] = []
        start = 0
        while start < total_pages:
            end = min(start + self._max_pages, total_pages)
            batches.append((start, end))
            start = end
        return batches

    async def _process_batch(
        self,
        source_path: pathlib.Path,
        book_id: str,
        workspace: BookWorkspace,
        batch_idx: int,
        page_start: int,
        page_end: int,
        total_batches: int,
    ) -> _BatchResult:
        """Process one batch: optionally split, submit, save raw, extract.

        Args:
            source_path: Original PDF path.
            book_id: Book id.
            workspace: Book workspace.
            batch_idx: Batch index (0-based).
            page_start: Global start page (0-based, inclusive).
            page_end: Global end page (0-based, exclusive).
            total_batches: Total number of batches.

        Returns:
            A :class:`_BatchResult` with markdown and span data.
        """
        # Determine the file to submit (split if needed).
        if total_batches == 1:
            submit_path = source_path
        else:
            submit_path = workspace.mineru_raw_dir / f"batch_{batch_idx}_pages_{page_start + 1}_{page_end}.pdf"
            self._split_pdf(source_path, submit_path, page_start, page_end)
            self.logger.info("pdf split for batch", batch_idx=batch_idx, path=str(submit_path))

        # Submit to MinerU and wait.
        assert self._client is not None
        data_id = f"{book_id}_batch_{batch_idx}"
        # When the PDF is already split, don't pass page_ranges — the split
        # file already contains only the target pages, and MinerU interprets
        # page_ranges relative to the submitted file, not the original PDF.
        batch_result = await self._client.submit_and_wait(
            file_path=submit_path,
            data_id=data_id,
            page_ranges=None,
            model_version=self._model_version,
        )

        # Save raw zip.
        raw_zip = workspace.mineru_raw_dir / f"batch_{batch_idx}.zip"
        async with aiofiles.open(raw_zip, "wb") as f:
            await f.write(batch_result.zip_bytes)
        self.logger.info("raw zip saved", batch_idx=batch_idx, path=str(raw_zip))

        # Extract markdown and layout from zip.
        md_text, layout_json = self._extract_from_zip(batch_result.zip_bytes, batch_idx)
        self.logger.info(
            "batch extracted",
            batch_idx=batch_idx,
            md_chars=len(md_text),
            has_layout=layout_json is not None,
        )

        return _BatchResult(
            batch_idx=batch_idx,
            page_start=page_start,
            page_end=page_end,
            markdown=md_text,
            layout_json=layout_json,
        )

    @staticmethod
    def _split_pdf(source_path: pathlib.Path, output_path: pathlib.Path, start: int, end: int) -> None:
        """Extract pages [start, end) from source PDF into output_path."""
        reader = PdfReader(str(source_path))
        writer = PdfWriter()
        for page_num in range(start, end):
            writer.add_page(reader.pages[page_num])
        with output_path.open("wb") as f:
            writer.write(f)

    def _extract_from_zip(
        self,
        zip_bytes: bytes,
        batch_idx: int,
    ) -> tuple[str, dict[str, t.Any] | list[t.Any] | None]:
        """Extract full.md and layout.json from a MinerU result ZIP.

        Args:
            zip_bytes: Raw ZIP bytes.
            batch_idx: Batch index for logging.

        Returns:
            A tuple of (markdown_text, layout_json_or_None).
        """
        md_text = ""
        layout_json: dict[str, t.Any] | None = None

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for name in zf.namelist():
                if name.endswith("/"):
                    continue
                lower = name.lower()
                if lower.endswith(".md") and not md_text:
                    md_text = zf.read(name).decode("utf-8", errors="replace")
                    self.logger.debug("found markdown in zip", batch_idx=batch_idx, name=name)
                elif ("layout" in lower or "middle" in lower) and lower.endswith(".json"):
                    try:
                        layout_json = json.loads(zf.read(name).decode("utf-8", errors="replace"))
                        self.logger.debug("found layout json in zip", batch_idx=batch_idx, name=name)
                    except json.JSONDecodeError as e:
                        self.logger.warning("layout json parse failed", batch_idx=batch_idx, name=name, error=str(e))

        if not md_text:
            self.logger.warning("no markdown found in zip", batch_idx=batch_idx)
        return md_text, layout_json

    def _merge_batches(
        self,
        book_id: str,
        batch_results: list[_BatchResult],
    ) -> tuple[str, list[PdfSourceMapping]]:
        """Merge per-batch markdown and spans into unified CONTENT.md + mappings.

        Each batch's markdown is concatenated. Span page indices are corrected
        by adding the batch's ``page_start``. Span content offsets are
        corrected by adding the accumulated markdown character offset.

        Args:
            book_id: Book id.
            batch_results: Per-batch extraction results.

        Returns:
            A tuple of (unified_content, source_mappings).
        """
        content_parts: list[str] = []
        all_mappings: list[PdfSourceMapping] = []
        content_offset = 0

        for br in batch_results:
            if not br.markdown:
                continue
            md = br.markdown
            content_parts.append(md)

            # Build mappings from layout spans.
            if br.layout_json is not None:
                spans = self._extract_spans(br.layout_json)
                batch_mappings = self._build_mappings_from_spans(
                    book_id=book_id,
                    spans=spans,
                    content_text=md,
                    content_offset_base=content_offset,
                    page_offset=br.page_start,
                )
                all_mappings.extend(batch_mappings)

            content_offset += len(md)

        content = "".join(content_parts)
        return content, all_mappings

    def _extract_spans(self, layout_json: dict[str, t.Any] | list[t.Any]) -> list[dict[str, t.Any]]:
        """Extract spans from a MinerU layout/middle JSON.

        Handles common structure variations:
        ``{pdf_info: [{page_idx, spans: [{bbox, content}]}]}``
        ``{data: [{page_idx, spans: [...]}}``

        Args:
            layout_json: Parsed layout JSON.

        Returns:
            Flat list of span dicts, each with ``page_idx``, ``bbox``, ``content``.
        """
        pages: list[dict[str, t.Any]] = []
        for key in ("pdf_info", "data", "pages", "page_info"):
            if isinstance(layout_json, dict) and key in layout_json and isinstance(layout_json[key], list):
                pages = layout_json[key]
                break
        if not pages and isinstance(layout_json, list):
            pages = layout_json

        spans: list[dict[str, t.Any]] = []
        for page in pages:
            if not isinstance(page, dict):
                continue  # type: ignore[unreachable]
            page_idx = int(page.get("page_idx", page.get("page_id", page.get("page_no", 0))))  # type: ignore[arg-type]
            page_spans = page.get("spans", [])
            if not page_spans:
                # Some formats nest spans inside para_blocks.
                page_spans = self._extract_spans_from_blocks(page.get("para_blocks", []))
            for span in page_spans:
                if not isinstance(span, dict):
                    continue
                content = span.get("content", span.get("text", ""))
                bbox = span.get("bbox", span.get("box", [0.0, 0.0, 0.0, 0.0]))
                if content and isinstance(bbox, (list | tuple)) and len(bbox) >= 4:
                    spans.append({
                        "page_idx": int(page_idx),
                        "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
                        "content": str(content),
                    })
        return spans

    @staticmethod
    def _extract_spans_from_blocks(blocks: list[dict[str, t.Any]]) -> list[dict[str, t.Any]]:
        """Recursively extract spans from para_blocks structure."""
        result: list[dict[str, t.Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue  # type: ignore[unreachable]
            if "spans" in block:
                result.extend(block["spans"])
            if "lines" in block:
                for line in block["lines"]:
                    if isinstance(line, dict) and "spans" in line:
                        result.extend(line["spans"])
            if "blocks" in block:
                result.extend(MineruPdfParser._extract_spans_from_blocks(block["blocks"]))
        return result

    def _build_mappings_from_spans(
        self,
        book_id: str,
        spans: list[dict[str, t.Any]],
        content_text: str,
        content_offset_base: int,
        page_offset: int,
    ) -> list[PdfSourceMapping]:
        """Build PDF source mappings by locating span text in the markdown.

        Uses sequential search: for each span (in order), find its text in
        ``content_text`` starting from a moving cursor. This is best-effort
        (spec §4.4 #5-6 allows incomplete mapping).

        Args:
            book_id: Book id.
            spans: Flat span list with page_idx, bbox, content.
            content_text: The batch's markdown text.
            content_offset_base: Character offset where this batch starts in
                the merged CONTENT.md.
            page_offset: Global page offset for this batch.

        Returns:
            PDF source mappings for this batch.
        """
        mappings: list[PdfSourceMapping] = []
        cursor = 0

        for span in spans:
            content = span["content"].strip()
            if not content:
                continue
            # Try to find the span text in the markdown.
            pos = self._find_text(content_text, content, cursor)
            if pos < 0:
                # Try first 30 chars as fallback.
                snippet = content[:30].strip()
                if snippet:
                    pos = self._find_text(content_text, snippet, cursor)
                if pos < 0:
                    self.logger.debug(
                        "span text not found in markdown, skipping",
                        page=span["page_idx"],
                        text_preview=content[:50],
                    )
                    continue
                length = len(snippet)
            else:
                length = len(content)

            bbox = span["bbox"]
            mappings.append(
                PdfSourceMapping(
                    book_id=book_id,
                    content_offset=content_offset_base + pos,
                    content_length=length,
                    page_index=span["page_idx"] + page_offset,
                    x0=bbox[0],
                    y0=bbox[1],
                    x1=bbox[2],
                    y1=bbox[3],
                    parser_name=PARSER_NAME,
                    parser_version=PARSER_VERSION,
                )
            )
            cursor = pos + length

        return mappings

    @staticmethod
    def _find_text(haystack: str, needle: str, start: int) -> int:
        """Find ``needle`` in ``haystack`` starting from ``start``.

        Normalizes whitespace for more robust matching.

        Args:
            haystack: The text to search in.
            needle: The text to search for.
            start: Starting position.

        Returns:
            Position in ``haystack``, or -1 if not found.
        """
        # Try exact match first.
        pos = haystack.find(needle, start)
        if pos >= 0:
            return pos
        # Try with collapsed whitespace.
        normalized = " ".join(needle.split())
        if normalized != needle:
            pos = haystack.find(normalized, start)
            if pos >= 0:
                return pos
        return -1

    @staticmethod
    async def _sha256(path: pathlib.Path) -> str:
        """Compute sha256 hex of a file."""
        hasher = hashlib.sha256()
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    async def lookup_source(
        self,
        mapping_db_path: pathlib.Path | str,
        content_offset: int,
    ) -> list[PdfSourceMapping]:
        """Open a mapping DB and look up mappings covering ``content_offset``.

        Args:
            mapping_db_path: Path to the PDF mapping SQLite.
            content_offset: Character offset in ``CONTENT.md``.

        Returns:
            Matching PDF source mappings.
        """
        store = PdfMappingStore(logger=self.logger, db_path=pathlib.Path(mapping_db_path))
        await store.startup()
        try:
            return await store.lookup(content_offset)  # type: ignore[no-any-return]
        finally:
            await store.shutdown()


class _BatchResult:
    """Internal container for one batch's extracted data."""

    __slots__ = ("batch_idx", "layout_json", "markdown", "page_end", "page_start")

    def __init__(
        self,
        batch_idx: int,
        page_start: int,
        page_end: int,
        markdown: str,
        layout_json: dict[str, t.Any] | list[t.Any] | None,
    ) -> None:
        self.batch_idx = batch_idx
        self.page_start = page_start
        self.page_end = page_end
        self.markdown = markdown
        self.layout_json = layout_json
