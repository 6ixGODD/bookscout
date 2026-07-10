# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
