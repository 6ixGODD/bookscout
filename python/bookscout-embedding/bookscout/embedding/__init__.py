from __future__ import annotations

import abc
import asyncio

from bookscout.core.mixins import AsyncResourceMixin


class EmbeddingSystem(AsyncResourceMixin, abc.ABC):
    """Provider-agnostic embedding backend.

    Implementations must supply :meth:`embed` and :meth:`embed_batch`.
    """

    @abc.abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Return the embedding vector for a single text."""

    async def embed_batch(self, texts: list[str], parallel: int = 5) -> list[list[float]]:
        """Return embedding vectors for a batch of texts.

        Args:
            texts: List of input texts.
            parallel: Maximum number of concurrent requests.
        """
        if parallel < 1:
            raise ValueError("parallel must be >= 1")
        if parallel == 1:
            return [await self.embed(text) for text in texts]

        semaphore = asyncio.Semaphore(parallel)
        results: list[list[float]] = []

        async def embed_with_semaphore(text: str) -> None:
            async with semaphore:
                embedding = await self.embed(text)
                results.append(embedding)

        await asyncio.gather(*(embed_with_semaphore(text) for text in texts))
        return results
