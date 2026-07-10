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
import typing as t

from bookscout.core.mixins import AsyncResourceMixin


class SearchResult(t.NamedTuple):
    """One vector-search hit returned by :class:`VectorStore.search`.

    Attributes:
        id: Unique point identifier.
        score: Similarity score (higher = more relevant).
        payload: Associated metadata dict.
    """

    id: str
    score: float
    payload: dict[str, t.Any]


class VectorStore(AsyncResourceMixin, abc.ABC):
    """Abstract vector store.  Concrete back-ends (Qdrant, LanceDB) subclass
    this.

    Use ``async with store:`` to initialise the connection, then call
    :meth:`upsert`, :meth:`search`, and :meth:`delete`.
    """

    @abc.abstractmethod
    async def upsert(
        self,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, t.Any]],
    ) -> None:
        """Insert or update points.

        Args:
            ids: Unique point identifiers.
            vectors: Float32 embedding vectors.
            payloads: Per-point metadata dicts.
        """

    @abc.abstractmethod
    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 10,
        filter: dict[str, t.Any] | None = None,  # pylint: disable=redefined-builtin
    ) -> list[SearchResult]:
        """Return the top-k nearest neighbours.

        Args:
            vector: Query embedding vector.
            top_k: Maximum number of results.
            filter: Optional metadata filter conditions.

        Returns:
            List of :class:`SearchResult` hits sorted by relevance.
        """

    @abc.abstractmethod
    async def delete(self, ids: list[str]) -> None:
        """Delete points by ID.

        Args:
            ids: Point identifiers to remove.
        """
