from __future__ import annotations

import asyncio

from openai import AsyncOpenAI
from openai import Omit
from pydantic import BaseModel
from pydantic import Field

from . import EmbeddingSystem


class OpenAIEmbeddingConfig(BaseModel):
    model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model name (default `text-embedding-3-small`).",
    )

    dimensions: int | None = Field(
        default=None,
        description="Embedding vector dimensionality (optional, inferred from model if not set).",
    )

    api_key: str | None = Field(
        default=None,
        description="OpenAI API key (optional, can also be set via environment variable).",
    )

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for the OpenAI API (default `https://api.openai.com/v1`).",
    )

    organization: str | None = Field(
        default=None,
        description="OpenAI organization ID (optional).",
    )

    project: str | None = Field(
        default=None,
        description="OpenAI project ID (optional).",
    )

    default_headers: dict[str, str] | None = Field(
        default=None,
        description="Default headers to include in all requests (optional).",
    )

    default_query: dict[str, str] | None = Field(
        default=None,
        description="Default query parameters to include in all requests (optional).",
    )

    timeout: float = Field(
        default=30.0,
        description="Request timeout in seconds (default 30.0).",
    )

    max_retries: int = Field(
        default=3,
        description="Maximum number of retries for failed requests (default 3).",
    )

    batch_size: int = Field(
        default=100,
        description="Maximum number of texts to embed in a single batch (default 100).",
    )


class OpenAIEmbedding(EmbeddingSystem):
    def __init__(self, config: OpenAIEmbeddingConfig) -> None:
        self.config = config
        self.client = AsyncOpenAI(
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            timeout=self.config.timeout,
            max_retries=self.config.max_retries,
            organization=self.config.organization,
            project=self.config.project,
            default_headers=self.config.default_headers,
            default_query=self.config.default_query,
        )

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string.

        Args:
            text: Input text to embed.

        Returns:
            Float32 embedding vector.
        """
        return (await self._embed_texts([text]))[0]

    async def embed_batch(self, texts: list[str], parallel: int = 5) -> list[list[float]]:
        """Embed a batch of texts, respecting ``batch_size``.

        Args:
            texts: List of input texts.
            parallel: Maximum number of concurrent batches to process (default 5).

        Returns:
            List of embedding vectors, one per input text.
        """
        bsz = self.config.batch_size
        result: list[list[float]] = []
        if parallel < 1:
            raise ValueError("`parallel` must be >= 1")
        if parallel == 1:
            for i in range(0, len(texts), bsz):
                result.extend(await self._embed_texts(texts[i : i + bsz]))
            return result
        semaphores = asyncio.Semaphore(parallel)

        async def _embed_batch(batch: list[str]) -> list[list[float]]:
            async with semaphores:
                return await self._embed_texts(batch)

        tasks = [_embed_batch(texts[i : i + bsz]) for i in range(0, len(texts), bsz)]
        for task in asyncio.as_completed(tasks):
            result.extend(await task)
        return result

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.config.model,
            input=texts,
            dimensions=self.config.dimensions or Omit(),
        )
        return [item.embedding for item in response.data]
