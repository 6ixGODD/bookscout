from __future__ import annotations

import typing as t

import lancedb
import pyarrow as pa

from bookscout.vectorstore import SearchResult
from bookscout.vectorstore import VectorStore


class LanceDBConfig(t.NamedTuple):
    """Configuration for :class:`LanceDBStore`.

    Attributes:
        uri: Path or URI for the LanceDB database directory.
        table_name: Name of the LanceDB table.
        vector_size: Vector dimensionality. ``None`` means auto-detect from
            the first upsert batch.
    """

    uri: str = "./lancedb_data"
    table_name: str = "xinglin"
    # vector_size=None means auto-detect from the first upsert batch
    vector_size: int | None = None


class LanceDBStore(VectorStore):
    """LanceDB-backed vector store (local, no Docker required).

    Vector dimensionality is detected automatically from the first batch
    written via :meth:`upsert`, so you never need to hard-code it.
    """

    def __init__(self, config: LanceDBConfig | None = None) -> None:
        self._config = config or LanceDBConfig()
        self._db: t.Any = None
        self._table: t.Any = None

    async def init(self) -> None:
        """Connect to LanceDB and open the table if it already exists."""
        self._db = await lancedb.connect_async(self._config.uri)
        table_names = await self._db.table_names()
        if self._config.table_name in table_names:
            self._table = await self._db.open_table(self._config.table_name)
        # If the table does not yet exist, creation is deferred to first upsert
        # so we can infer the vector dimensionality from the actual data.

    async def _ensure_table(self, dim: int) -> None:
        """Create the LanceDB table if it hasn't been created yet.

        Args:
            dim: Vector dimensionality (inferred from the first batch).
        """
        if self._table is not None:
            return
        schema = pa.schema([
            pa.field("_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
            pa.field("payload_json", pa.string()),
        ])
        self._table = await self._db.create_table(
            self._config.table_name,
            schema=schema,
        )

    async def close(self) -> None:
        """Release the database and table references."""
        self._db = None
        self._table = None

    async def upsert(
        self,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, t.Any]],
    ) -> None:
        import json

        if vectors:
            await self._ensure_table(len(vectors[0]))

        rows = [
            {
                "_id": id_,
                "vector": [float(x) for x in vec],
                "payload_json": json.dumps(payload),
            }
            for id_, vec, payload in zip(ids, vectors, payloads, strict=False)
        ]
        # LanceDB upsert by merging on _id
        await self._table.merge_insert("_id").when_matched_update_all().when_not_matched_insert_all().execute(rows)

    async def search(
        self,
        vector: list[float],
        *,
        top_k: int = 10,
        filter: dict[str, t.Any] | None = None,  # pylint: disable=redefined-builtin
    ) -> list[SearchResult]:
        import json  # pylint: disable=import-outside-toplevel

        # In LanceDB 0.30+, AsyncTable.search() returns a coroutine that
        # resolves to an AsyncVectorQuery builder; we must await it before
        # chaining .limit() / .where().
        q = await self._table.search(vector)
        q = q.limit(top_k)
        if filter:
            conditions = " AND ".join(f"json_extract(payload_json, '$.{k}') = '{v}'" for k, v in filter.items())
            q = q.where(conditions)
        rows = await q.to_list()
        return [
            SearchResult(
                id=row["_id"],
                score=1.0 - row.get("_distance", 0.0),
                payload=json.loads(row.get("payload_json", "{}")),
            )
            for row in rows
        ]

    async def delete(self, ids: list[str]) -> None:
        id_list = ", ".join(f"'{i}'" for i in ids)
        await self._table.delete(f"_id IN ({id_list})")
