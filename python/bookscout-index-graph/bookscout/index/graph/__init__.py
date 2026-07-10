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

# pylint: disable=too-many-lines
"""Graph Index — knowledge graph extraction + retrieval with multi-hop (spec §11.3).

Extracts entities and relationships from node content via LLM, generates
embeddings for entity summaries, relationship summaries, and source chunks,
merges duplicate entities (within and across subgraphs), and provides
entity-first / relationship-first retrieval with vector + multi-hop and
FTS + multi-hop support.

Relationships use entity_id (not entity_name) to reference entities,
so that same-name-different-type entities are not conflated.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import pathlib
import typing as t

from sqlmodel import select

from bookscout.core.lib.utils import gen_id
from bookscout.doccompiler.indexer import IndexResult
from bookscout.doccompiler.indexer import Indexer
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .models import EntityModel
from .models import RelationshipModel

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler.workspace import BookWorkspace
    from bookscout.embedding import EmbeddingSystem
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger
    from bookscout.vectorstore.lancedb import LanceDBStore

DEFAULT_GRAPH_TOKEN_BUDGET = 1000
DEFAULT_ENTITY_MERGE_THRESHOLD = 0.85
_CHARS_PER_TOKEN = 4


# ------------------------------------------------------------------ Types


class EntityType(enum.StrEnum):
    """Closed set of entity types (spec §11.4)."""

    PERSON = "Person"
    ORGANIZATION = "Organization"
    PLACE = "Place"
    TIME = "Time"
    EVENT = "Event"
    WORK = "Work"
    OBJECT = "Object"
    CONCEPT = "Concept"
    QUANTITY = "Quantity"
    UNKNOWN = "Unknown"

    @classmethod
    def from_str(cls, value: str) -> EntityType:
        """Parse a string into an EntityType, defaulting to Unknown."""
        for member in cls:
            if member.value.lower() == value.lower():
                return member
        return cls.UNKNOWN


@dataclasses.dataclass(frozen=True, slots=True)
class Entity:
    """A knowledge graph entity (spec §11.3-11.5).

    Attributes:
        id: Unique entity id.
        book_id: Owning book id.
        name: Entity name (canonical).
        entity_type: One of :class:`EntityType`.
        tags: Open list of tag strings.
        summary: Entity summary text.
        source_chunk_id: The chunk this entity was extracted from.
        source_node_id: The node this entity was extracted from.
    """

    id: str
    book_id: str
    name: str
    entity_type: EntityType
    tags: list[str]
    summary: str
    source_chunk_id: str
    source_node_id: str


@dataclasses.dataclass(frozen=True, slots=True)
class Relationship:
    """A knowledge graph relationship (spec §11.3).

    Uses entity_id to reference source and target entities.

    Attributes:
        id: Unique relationship id.
        book_id: Owning book id.
        source_entity_id: Id of the source entity.
        target_entity_id: Id of the target entity.
        relation_type: Type of relationship.
        summary: Relationship summary text.
        claims: List of claim strings.
        source_chunk_id: Source chunk id.
        source_node_id: Source node id.
        content_offset: Character offset in CONTENT.md.
        content_length: Character length in CONTENT.md.
    """

    id: str
    book_id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    summary: str
    claims: list[str]
    source_chunk_id: str
    source_node_id: str
    content_offset: int
    content_length: int


# ------------------------------------------------------------------ Graph Store


class GraphStore(LoggingMixin):
    """SQLite-backed store for knowledge graph entities and relationships."""

    def __init__(self, logger: Logger, db_path: pathlib.Path) -> None:
        super().__init__(logger=logger)
        self._db_path = db_path
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{db_path.as_posix()}"),
            logger=logger,
        )

    async def startup(self) -> None:
        await self._sqlite.startup()
        await self._sqlite.create_all([EntityModel, RelationshipModel])

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()

    async def upsert_entities(self, entities: t.Sequence[Entity]) -> None:
        async with self._sqlite.session() as session:
            for ent in entities:
                existing = await session.get(EntityModel, ent.id)
                if existing is not None:
                    existing.name = ent.name
                    existing.entity_type = ent.entity_type.value
                    existing.tags_json = ent.tags
                    existing.summary = ent.summary
                    existing.source_chunk_id = ent.source_chunk_id
                    existing.source_node_id = ent.source_node_id
                else:
                    session.add(
                        EntityModel(
                            id=ent.id,
                            book_id=ent.book_id,
                            name=ent.name,
                            entity_type=ent.entity_type.value,
                            tags_json=ent.tags,
                            summary=ent.summary,
                            source_chunk_id=ent.source_chunk_id,
                            source_node_id=ent.source_node_id,
                        )
                    )
            await session.commit()

    async def upsert_relationships(self, relationships: t.Sequence[Relationship]) -> None:
        async with self._sqlite.session() as session:
            for rel in relationships:
                existing = await session.get(RelationshipModel, rel.id)
                if existing is not None:
                    existing.source_entity_id = rel.source_entity_id
                    existing.target_entity_id = rel.target_entity_id
                    existing.relation_type = rel.relation_type
                    existing.summary = rel.summary
                    existing.claims_json = rel.claims
                    existing.source_chunk_id = rel.source_chunk_id
                    existing.source_node_id = rel.source_node_id
                    existing.content_offset = rel.content_offset
                    existing.content_length = rel.content_length
                else:
                    session.add(
                        RelationshipModel(
                            id=rel.id,
                            book_id=rel.book_id,
                            source_entity_id=rel.source_entity_id,
                            target_entity_id=rel.target_entity_id,
                            relation_type=rel.relation_type,
                            summary=rel.summary,
                            claims_json=rel.claims,
                            source_chunk_id=rel.source_chunk_id,
                            source_node_id=rel.source_node_id,
                            content_offset=rel.content_offset,
                            content_length=rel.content_length,
                        )
                    )
            await session.commit()

    async def get_all_entities(
        self,
        book_id: str,
        *,
        node_ids: list[str] | None = None,
    ) -> list[Entity]:
        async with self._sqlite.session() as session:
            stmt = select(EntityModel).where(EntityModel.book_id == book_id)
            if node_ids:
                stmt = stmt.where(EntityModel.source_node_id.in_(node_ids))
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_entity(r) for r in rows]

    async def get_all_relationships(
        self,
        book_id: str,
        *,
        node_ids: list[str] | None = None,
    ) -> list[Relationship]:
        async with self._sqlite.session() as session:
            stmt = select(RelationshipModel).where(RelationshipModel.book_id == book_id)
            if node_ids:
                stmt = stmt.where(RelationshipModel.source_node_id.in_(node_ids))
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_relationship(r) for r in rows]

    async def get_entity(self, entity_id: str) -> Entity | None:
        async with self._sqlite.session() as session:
            row = await session.get(EntityModel, entity_id)
            return self._row_to_entity(row) if row else None

    async def search_entities_by_name(self, book_id: str, name: str) -> list[Entity]:
        async with self._sqlite.session() as session:
            stmt = select(EntityModel).where(
                EntityModel.book_id == book_id,
                EntityModel.name == name,
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_entity(r) for r in rows]

    async def get_relationships_for_entity(self, entity_id: str) -> list[Relationship]:
        """Get all relationships where the entity is source or target (by entity_id)."""
        async with self._sqlite.session() as session:
            stmt = select(RelationshipModel).where(
                (RelationshipModel.source_entity_id == entity_id) | (RelationshipModel.target_entity_id == entity_id),
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_relationship(r) for r in rows]

    async def get_entities_by_ids(self, entity_ids: t.Sequence[str]) -> list[Entity]:
        """Get entities by a batch of ids."""
        if not entity_ids:
            return []
        async with self._sqlite.session() as session:
            result: list[Entity] = []
            for eid in entity_ids:
                row = await session.get(EntityModel, eid)
                if row:
                    result.append(self._row_to_entity(row))
            return result

    async def fts_search_entities(self, query: str, limit: int = 10) -> list[Entity]:
        """Full-text search over entity names and summaries."""
        from sqlalchemy import text

        async with self._sqlite.session() as session:
            sql = text("SELECT * FROM entities WHERE name LIKE :q OR summary LIKE :q2 LIMIT :lim")
            result = await session.execute(sql, {"q": f"%{query}%", "q2": f"%{query}%", "lim": limit})
            rows = result.all()
            return [
                Entity(
                    id=r[0],
                    book_id=r[1],
                    name=r[2],
                    entity_type=EntityType.from_str(r[3]),
                    tags=list(r[4]) if r[4] else [],
                    summary=r[5],
                    source_chunk_id=r[6],
                    source_node_id=r[7],
                )
                for r in rows
            ]

    async def fts_search_relationships(self, query: str, limit: int = 10) -> list[Relationship]:
        """Full-text search over relationship summaries and types."""
        from sqlalchemy import text as sa_text

        async with self._sqlite.session() as session:
            sql = sa_text("SELECT * FROM relationships WHERE summary LIKE :q OR relation_type LIKE :q2 LIMIT :lim")
            result = await session.execute(
                sql,
                {
                    "q": f"%{query}%",
                    "q2": f"%{query}%",
                    "lim": limit,
                },
            )
            rows = result.all()
            return [
                Relationship(
                    id=r[0],
                    book_id=r[1],
                    source_entity_id=r[2],
                    target_entity_id=r[3],
                    relation_type=r[4],
                    summary=r[5],
                    claims=list(r[6]) if r[6] else [],
                    source_chunk_id=r[7],
                    source_node_id=r[8],
                    content_offset=r[9],
                    content_length=r[10],
                )
                for r in rows
            ]

    async def delete_all(self, book_id: str) -> None:
        async with self._sqlite.session() as session:
            for model_cls in [EntityModel, RelationshipModel]:
                stmt = select(model_cls).where(model_cls.book_id == book_id)  # type: ignore[attr-defined]
                for row in (await session.execute(stmt)).scalars().all():
                    await session.delete(row)
            await session.commit()

    @staticmethod
    def _row_to_entity(r: t.Any) -> Entity:
        return Entity(
            id=r.id,
            book_id=r.book_id,
            name=r.name,
            entity_type=EntityType.from_str(r.entity_type),
            tags=list(r.tags_json) if r.tags_json else [],
            summary=r.summary,
            source_chunk_id=r.source_chunk_id,
            source_node_id=r.source_node_id,
        )

    @staticmethod
    def _row_to_relationship(r: t.Any) -> Relationship:
        return Relationship(
            id=r.id,
            book_id=r.book_id,
            source_entity_id=r.source_entity_id,
            target_entity_id=r.target_entity_id,
            relation_type=r.relation_type,
            summary=r.summary,
            claims=list(r.claims_json) if r.claims_json else [],
            source_chunk_id=r.source_chunk_id,
            source_node_id=r.source_node_id,
            content_offset=r.content_offset,
            content_length=r.content_length,
        )


# ------------------------------------------------------------------ Internal types


@dataclasses.dataclass(slots=True)
class _RawEntity:
    """An entity extracted by the LLM, before dedup/merge."""

    name: str
    entity_type: str
    tags: list[str]
    summary: str
    source_chunk_id: str
    source_node_id: str


@dataclasses.dataclass(slots=True)
class _RawRelationship:
    """A relationship extracted by the LLM, before persistence."""

    source_name: str
    target_name: str
    relation_type: str
    summary: str
    claims: list[str]
    source_chunk_id: str
    source_node_id: str
    content_offset: int
    content_length: int


@dataclasses.dataclass(slots=True)
class _GraphChunk:
    """A chunk of content for graph extraction."""

    chunk_id: str
    text: str
    node_id: str
    content_offset: int
    content_length: int


# ------------------------------------------------------------------ Graph Indexer

_GRAPH_SYSTEM_PROMPT = """\
You are a knowledge graph extractor. Given a text passage, extract entities and relationships.

Entity types (MUST use one of these):
- Person: 人物
- Organization: 组织/机构/国家/团体
- Place: 地点/地理区域/空间
- Time: 时间/时期/年代
- Event: 事件/过程/行动/变化
- Work: 作品/文献/法律/书籍/理论文本
- Object: 具体物/产品/工具/设备/资源
- Concept: 概念/理论/思想/制度/方法/现象
- Quantity: 数值/指标/比例/金额/统计量
- Unknown: 无法判断

Return ONLY a JSON object with this structure:
{
  "entities": [
    {"name": "entity name", "type": "Person", "tags": ["tag1"], "summary": "brief description"}
  ],
  "relationships": [
    {"source": "entity name", "target": "entity name", "type": "relation type", "summary": "brief description", "claims": ["claim 1"]}
  ]
}

Rules:
- Entity names should be canonical (normalized) forms.
- Extract at most 10 entities and 10 relationships per passage.
- Only extract entities that are clearly present in the text.
- Relationship type is a free-form short phrase.
"""


class GraphIndexer(Indexer):
    """Builds a Graph Index via LLM extraction + embedding + merge (spec §11.3).

    Args:
        logger: Logger instance.
        books_store: The BooksStore to read node content from.
        model: A started ChatModel for LLM entity/relationship extraction.
        embedding: The embedding system for summary vectors.
        vector_store: The LanceDB vector store.
        token_budget: Max tokens per graph chunk.
        merge_threshold: Cosine similarity threshold for cross-subgraph entity merge.
        max_hop: Max hops for multi-hop retrieval.
        estimate_token_fn: Optional callable for token estimation.
    """

    def __init__(
        self,
        logger: Logger,
        books_store: BooksStore,
        model: ChatModel,
        embedding: EmbeddingSystem,
        vector_store: LanceDBStore,
        token_budget: int = DEFAULT_GRAPH_TOKEN_BUDGET,
        merge_threshold: float = DEFAULT_ENTITY_MERGE_THRESHOLD,
        max_hop: int = 2,
        estimate_token_fn: t.Callable[[str], int] | None = None,
    ) -> None:
        super().__init__(logger=logger, books_store=books_store)
        self._model = model
        self._embedding = embedding
        self._vector_store = vector_store
        self._token_budget = token_budget
        self._merge_threshold = merge_threshold
        self._max_hop = max_hop
        self._estimate_token = estimate_token_fn

    @property
    def index_type(self) -> str:
        return "graph"

    def _est_tokens(self, text: str) -> int:
        if self._estimate_token is not None:
            return self._estimate_token(text)
        return max(1, len(text) // _CHARS_PER_TOKEN)

    async def build_index(
        self,
        book_id: str,
        workspace: BookWorkspace,
        *,
        monitor: t.Any = None,
        parent_id: str | None = None,
    ) -> IndexResult:
        """Build the graph index for a book."""
        mtid = monitor.start("graph:extract", total=0, parent_id=parent_id) if monitor else None
        tree = await self._books_store.get_tree(book_id)
        self.logger.info("graph build starting", book_id=book_id, nodes=len(tree))
        self._update_progress(total=0, processed=0, status="running", error="")

        graph_store = GraphStore(logger=self.logger, db_path=workspace.index_db_path("graph"))
        await graph_store.startup()
        try:
            # 1. Split content into graph chunks by token budget.
            graph_chunks: list[_GraphChunk] = []
            for node in tree:
                if node.content_length <= 0:
                    continue
                content = await self._books_store.read_node_content(node.id)
                if not content.strip():
                    continue
                node_chunks = self._split_by_tokens(content, node.content_offset)
                for chunk_text, chunk_offset, chunk_len in node_chunks:
                    graph_chunks.append(
                        _GraphChunk(
                            chunk_id=gen_id(prefix="gchunk_"),
                            text=chunk_text,
                            node_id=node.id,
                            content_offset=chunk_offset,
                            content_length=chunk_len,
                        )
                    )

            self.logger.info("graph chunks split", count=len(graph_chunks))
            self._update_progress(total=len(graph_chunks), processed=0)
            if monitor and mtid:
                monitor.set_total(mtid, len(graph_chunks))

            # 2. LLM-extract entities + relationships from each chunk.
            all_raw_entities: list[_RawEntity] = []
            all_raw_relationships: list[_RawRelationship] = []
            for i, gc in enumerate(graph_chunks):
                raw_ents, raw_rels = await self._extract_chunk(gc)
                all_raw_entities.extend(raw_ents)
                all_raw_relationships.extend(raw_rels)
                self._update_progress(processed=i + 1)
                if monitor and mtid:
                    monitor.advance(mtid, 1)

            self.logger.info(
                "raw extraction done",
                entities=len(all_raw_entities),
                relationships=len(all_raw_relationships),
            )

            # 3. Merge entities by exact name (§11.3 #9).
            merged_entities = self._merge_entities_by_name(all_raw_entities)

            # 4. Embed entity summaries.
            entity_summaries = [e.summary or e.name for e in merged_entities]
            entity_vectors = await self._embed_batch_safe(entity_summaries) if entity_summaries else []

            # 5. Cross-subgraph merge by similarity (§11.3 #10).
            if entity_vectors and len(entity_vectors) > 1:
                merged_entities, entity_vectors = self._merge_entities_by_similarity(
                    merged_entities,
                    entity_vectors,
                    self._merge_threshold,
                )

            # 6. Persist entities and build name→id map.
            entities: list[Entity] = []
            for raw in merged_entities:
                ent = Entity(
                    id=gen_id(prefix="ent_"),
                    book_id=book_id,
                    name=raw.name,
                    entity_type=EntityType.from_str(raw.entity_type),
                    tags=raw.tags,
                    summary=raw.summary,
                    source_chunk_id=raw.source_chunk_id,
                    source_node_id=raw.source_node_id,
                )
                entities.append(ent)
            await graph_store.upsert_entities(entities)

            # Build name→entity_id map for relationship linkage.
            name_to_entity_id: dict[str, str] = {}
            for ent in entities:
                name_to_entity_id[ent.name.lower().strip()] = ent.id

            # 7. Store entity embeddings in LanceDB (spec §11.3 #6).
            if entities and entity_vectors:
                await self._vector_store.upsert(
                    [e.id for e in entities],
                    entity_vectors[: len(entities)],
                    [
                        {
                            "book_id": e.book_id,
                            "name": e.name,
                            "type": e.entity_type.value,
                            "summary": e.summary,
                            "kind": "entity",
                            "entity_id": e.id,
                            "source_chunk_id": e.source_chunk_id,
                            "source_node_id": e.source_node_id,
                        }
                        for e in entities
                    ],
                )

            # 8. Build and persist relationships using entity_id.
            relationships: list[Relationship] = []
            for raw_rel in all_raw_relationships:
                src_id = name_to_entity_id.get(raw_rel.source_name.lower().strip(), "")
                tgt_id = name_to_entity_id.get(raw_rel.target_name.lower().strip(), "")
                if not src_id or not tgt_id:
                    self.logger.debug(
                        "relationship dropped — entity not found",
                        source=raw_rel.source_name,
                        target=raw_rel.target_name,
                    )
                    continue
                relationships.append(
                    Relationship(
                        id=gen_id(prefix="rel_"),
                        book_id=book_id,
                        source_entity_id=src_id,
                        target_entity_id=tgt_id,
                        relation_type=raw_rel.relation_type,
                        summary=raw_rel.summary,
                        claims=raw_rel.claims,
                        source_chunk_id=raw_rel.source_chunk_id,
                        source_node_id=raw_rel.source_node_id,
                        content_offset=raw_rel.content_offset,
                        content_length=raw_rel.content_length,
                    )
                )
            await graph_store.upsert_relationships(relationships)

            # 9. Embed and store relationship summaries (spec §11.3 #7).
            if relationships:
                rel_summaries = [r.summary or f"relationship {r.id}" for r in relationships]
                rel_vectors = await self._embed_batch_safe(rel_summaries)
                await self._vector_store.upsert(
                    [r.id for r in relationships],
                    rel_vectors,
                    [
                        {
                            "book_id": r.book_id,
                            "source_entity_id": r.source_entity_id,
                            "target_entity_id": r.target_entity_id,
                            "type": r.relation_type,
                            "summary": r.summary,
                            "kind": "relationship",
                            "relationship_id": r.id,
                        }
                        for r in relationships
                    ],
                )

            # 10. Embed and store source chunk texts (spec §11.3 #8).
            if graph_chunks:
                chunk_texts = [gc.text for gc in graph_chunks]
                chunk_vectors = await self._embed_batch_safe(chunk_texts)
                await self._vector_store.upsert(
                    [gc.chunk_id for gc in graph_chunks],
                    chunk_vectors,
                    [
                        {
                            "book_id": book_id,
                            "node_id": gc.node_id,
                            "text": gc.text[:200],
                            "kind": "graph_chunk",
                            "chunk_id": gc.chunk_id,
                            "content_offset": gc.content_offset,
                            "content_length": gc.content_length,
                        }
                        for gc in graph_chunks
                    ],
                )

            total = len(entities) + len(relationships)
            self._update_progress(total=total, processed=total, status="done")
            self.logger.info(
                "graph build finished",
                book_id=book_id,
                entities=len(entities),
                relationships=len(relationships),
                chunks=len(graph_chunks),
            )
            if monitor and mtid:
                monitor.finish(mtid)
            return IndexResult(index_type="graph", count=total, progress=self.progress)
        finally:
            await graph_store.shutdown()

    async def _embed_batch_safe(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts, preserving order (parallel=1)."""
        if not texts:
            return []
        return await self._embedding.embed_batch(texts, parallel=1)  # type: ignore[no-any-return]

    async def _extract_chunk(self, gc: _GraphChunk) -> tuple[list[_RawEntity], list[_RawRelationship]]:
        """Extract entities and relationships from one graph chunk via LLM."""
        from bookscout.llm.types import CompletionOptions
        from bookscout.llm.types import SystemMessage
        from bookscout.llm.types import UserMessage

        try:
            response = await self._model.chat_completion(
                [
                    SystemMessage(content=_GRAPH_SYSTEM_PROMPT),
                    UserMessage(content=f"Extract entities and relationships from this text:\n\n{gc.text}"),
                ],
                options=CompletionOptions(max_tokens=2048, temperature=0.0),
            )
            raw_text = response["message"].content.strip()
            parsed = self._parse_json(raw_text)
            if parsed is None:
                self.logger.warning("failed to parse graph extraction", node_id=gc.node_id)
                return [], []

            raw_entities: list[_RawEntity] = []
            for ent in parsed.get("entities", []):
                name = str(ent.get("name", "")).strip()
                if not name:
                    continue
                raw_entities.append(
                    _RawEntity(
                        name=name,
                        entity_type=str(ent.get("type", "Unknown")),
                        tags=[str(tg) for tg in ent.get("tags", [])],
                        summary=str(ent.get("summary", "")),
                        source_chunk_id=gc.chunk_id,
                        source_node_id=gc.node_id,
                    )
                )

            raw_relationships: list[_RawRelationship] = []
            for rel in parsed.get("relationships", []):
                source = str(rel.get("source", "")).strip()
                target = str(rel.get("target", "")).strip()
                if not source or not target:
                    continue
                raw_relationships.append(
                    _RawRelationship(
                        source_name=source,
                        target_name=target,
                        relation_type=str(rel.get("type", "")),
                        summary=str(rel.get("summary", "")),
                        claims=[str(c) for c in rel.get("claims", [])],
                        source_chunk_id=gc.chunk_id,
                        source_node_id=gc.node_id,
                        content_offset=gc.content_offset,
                        content_length=gc.content_length,
                    )
                )
            return raw_entities, raw_relationships
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.warning("graph extraction failed", node_id=gc.node_id, error=str(e))
            return [], []

    @staticmethod
    def _parse_json(raw: str) -> dict[str, t.Any] | None:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0:
            return None
        try:
            return json.loads(text[start : end + 1])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            return None

    def _split_by_tokens(self, content: str, base_offset: int) -> list[tuple[str, int, int]]:
        if not content:
            return []
        chunks: list[tuple[str, int, int]] = []
        pos = 0
        while pos < len(content):
            remaining = content[pos:]
            remaining_tokens = self._est_tokens(remaining)
            if remaining_tokens <= self._token_budget:
                chunks.append((remaining, base_offset + pos, len(remaining)))
                break
            char_estimate = int(len(remaining) * self._token_budget / remaining_tokens)
            char_estimate = max(100, min(char_estimate, len(remaining)))
            cut_pos = self._find_cut_point(remaining, char_estimate)
            chunks.append((remaining[:cut_pos], base_offset + pos, cut_pos))
            pos += cut_pos
        return chunks

    @staticmethod
    def _find_cut_point(text: str, target_pos: int) -> int:
        search_start = max(0, target_pos - 200)
        search_end = min(len(text), target_pos + 200)
        para_break = text.rfind("\n\n", search_start, search_end)
        if para_break > 0:
            return para_break + 2
        sent_break = text.rfind("。", search_start, search_end)
        if sent_break < 0:
            sent_break = text.rfind(".", search_start, search_end)
        if sent_break > 0:
            return sent_break + 1
        nl_break = text.rfind("\n", search_start, search_end)
        if nl_break > 0:
            return nl_break + 1
        return target_pos

    @staticmethod
    def _merge_entities_by_name(raw_entities: list[_RawEntity]) -> list[_RawEntity]:
        by_name: dict[str, _RawEntity] = {}
        for raw in raw_entities:
            key = raw.name.lower().strip()
            if key in by_name:
                existing = by_name[key]
                for tag in raw.tags:
                    if tag not in existing.tags:
                        existing.tags.append(tag)
                if len(raw.summary) > len(existing.summary):
                    existing.summary = raw.summary
            else:
                by_name[key] = _RawEntity(
                    name=raw.name,
                    entity_type=raw.entity_type,
                    tags=list(raw.tags),
                    summary=raw.summary,
                    source_chunk_id=raw.source_chunk_id,
                    source_node_id=raw.source_node_id,
                )
        return list(by_name.values())

    def _merge_entities_by_similarity(
        self,
        entities: list[_RawEntity],
        vectors: list[list[float]],
        threshold: float,
    ) -> tuple[list[_RawEntity], list[list[float]]]:
        if not entities or not vectors:
            return entities, vectors
        merged: list[_RawEntity] = []
        merged_vectors: list[list[float]] = []
        used: set[int] = set()
        for i, ent in enumerate(entities):
            if i in used:
                continue
            current = _RawEntity(
                name=ent.name,
                entity_type=ent.entity_type,
                tags=list(ent.tags),
                summary=ent.summary,
                source_chunk_id=ent.source_chunk_id,
                source_node_id=ent.source_node_id,
            )
            current_vec = vectors[i]
            for j in range(i + 1, len(entities)):
                if j in used:
                    continue
                sim = self._cosine_similarity(current_vec, vectors[j])
                if sim >= threshold:
                    for tag in entities[j].tags:
                        if tag not in current.tags:
                            current.tags.append(tag)
                    used.add(j)
            merged.append(current)
            merged_vectors.append(current_vec)
        return merged, merged_vectors

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)  # type: ignore[no-any-return]

    async def entity_first_retrieval(
        self,
        query: str,
        book_id: str,
        graph_store: GraphStore,
        top_k: int = 5,
        max_hop: int | None = None,
    ) -> list[dict[str, t.Any]]:
        """Entity-first retrieval: vector search entities → multi-hop expand by entity_id.

        1. Embed query, vector-search entities in LanceDB.
        2. For each hit entity, look up relationships by entity_id from GraphStore.
        3. For each related entity (by entity_id), repeat up to max_hop.

        Each result includes the entity's source_chunk_id for chunk traceback.

        Args:
            query: The search query.
            book_id: Book id to filter.
            graph_store: An open GraphStore.
            top_k: Number of seed entities.
            max_hop: Max hops. Defaults to self._max_hop.

        Returns:
            List of result dicts with entity + relationships + source_chunk_id.
        """
        hops = max_hop if max_hop is not None else self._max_hop
        query_vec = await self._embedding.embed(query)
        results = await self._vector_store.search(query_vec, top_k=top_k * 2)
        seed_hits = [r for r in results if r.payload.get("kind") == "entity" and r.payload.get("book_id") == book_id][
            :top_k
        ]

        visited: set[str] = set()
        output: list[dict[str, t.Any]] = []
        # Frontier: (entity_id, entity_payload, score)
        frontier: list[tuple[str, dict[str, t.Any], float]] = [
            (hit.payload.get("entity_id", hit.id), hit.payload, hit.score) for hit in seed_hits
        ]

        for _hop in range(hops + 1):
            next_frontier: list[tuple[str, dict[str, t.Any], float]] = []
            for entity_id, entity_payload, score in frontier:
                if entity_id in visited or not entity_id:
                    continue
                visited.add(entity_id)

                # Look up relationships by entity_id.
                relationships = await graph_store.get_relationships_for_entity(entity_id)
                # Resolve related entity ids for multi-hop + display.
                rel_data: list[dict[str, t.Any]] = []
                related_ids: set[str] = set()
                for r in relationships:
                    other_id = r.target_entity_id if r.source_entity_id == entity_id else r.source_entity_id
                    related_ids.add(other_id)
                    rel_data.append({
                        "relationship_id": r.id,
                        "source_entity_id": r.source_entity_id,
                        "target_entity_id": r.target_entity_id,
                        "type": r.relation_type,
                        "summary": r.summary,
                        "source_chunk_id": r.source_chunk_id,
                    })

                output.append({
                    "entity": entity_payload,
                    "entity_id": entity_id,
                    "score": score,
                    "hop": _hop,
                    "source_chunk_id": entity_payload.get("source_chunk_id", ""),
                    "relationships": rel_data,
                })

                # Add related entities to next frontier.
                for rid in related_ids:
                    if rid not in visited:
                        next_frontier.append((rid, {"entity_id": rid, "kind": "entity"}, 0.0))

            frontier = next_frontier
            if not frontier:
                break

        return output

    async def relationship_first_retrieval(
        self,
        query: str,
        book_id: str,
        graph_store: GraphStore,
        top_k: int = 5,
        max_hop: int | None = None,
    ) -> list[dict[str, t.Any]]:
        """Relationship-first retrieval: vector search relationships → multi-hop by entity_id.

        1. Embed query, vector-search relationships in LanceDB.
        2. For each hit, resolve source/target entities by entity_id.
        3. For each connected entity, expand their relationships up to max_hop.

        Args:
            query: The search query.
            book_id: Book id to filter.
            graph_store: An open GraphStore.
            top_k: Number of seed relationships.
            max_hop: Max hops. Defaults to self._max_hop.

        Returns:
            List of result dicts with relationship + connected entities.
        """
        hops = max_hop if max_hop is not None else self._max_hop
        query_vec = await self._embedding.embed(query)
        results = await self._vector_store.search(query_vec, top_k=top_k * 2)
        seed_rels = [
            r for r in results if r.payload.get("kind") == "relationship" and r.payload.get("book_id") == book_id
        ][:top_k]

        output: list[dict[str, t.Any]] = []
        visited_entities: set[str] = set()

        for hit in seed_rels:
            src_id = hit.payload.get("source_entity_id", "")
            tgt_id = hit.payload.get("target_entity_id", "")

            src_ent = await graph_store.get_entity(src_id) if src_id else None
            tgt_ent = await graph_store.get_entity(tgt_id) if tgt_id else None

            output.append({
                "relationship": hit.payload,
                "score": hit.score,
                "hop": 0,
                "source_entity": self._entity_dict(src_ent) if src_ent else None,
                "target_entity": self._entity_dict(tgt_ent) if tgt_ent else None,
            })

            # Multi-hop: expand from source and target entities.
            if hops > 0:
                for eid in [src_id, tgt_id]:
                    if eid in visited_entities or not eid:
                        continue
                    visited_entities.add(eid)
                    ent_rels = await graph_store.get_relationships_for_entity(eid)
                    for r in ent_rels[:3]:
                        other_id = r.target_entity_id if r.source_entity_id == eid else r.source_entity_id
                        if other_id not in visited_entities:
                            other_ent = await graph_store.get_entity(other_id)
                            output.append({
                                "relationship": {
                                    "source_entity_id": r.source_entity_id,
                                    "target_entity_id": r.target_entity_id,
                                    "type": r.relation_type,
                                    "summary": r.summary,
                                    "kind": "relationship",
                                },
                                "score": 0.0,
                                "hop": 1,
                                "source_entity": None,
                                "target_entity": self._entity_dict(other_ent) if other_ent else None,
                            })

        return output

    async def fts_entity_retrieval(
        self,
        query: str,
        book_id: str,  # pylint: disable=unused-argument
        graph_store: GraphStore,
        limit: int = 10,
        max_hop: int | None = None,
    ) -> list[dict[str, t.Any]]:
        """FTS + multi-hop: full-text search entities → expand relationships by entity_id.

        Args:
            query: The search query.
            book_id: Book id to filter.
            graph_store: An open GraphStore.
            limit: Max entities from FTS.
            max_hop: Max hops. Defaults to self._max_hop.

        Returns:
            List of result dicts with entity + expanded relationships.
        """
        hops = max_hop if max_hop is not None else self._max_hop
        entities = await graph_store.fts_search_entities(query, limit=limit)

        output: list[dict[str, t.Any]] = []
        visited: set[str] = set()

        for ent in entities:
            if ent.id in visited:
                continue
            visited.add(ent.id)
            relationships = await graph_store.get_relationships_for_entity(ent.id)
            output.append({
                "entity": self._entity_dict(ent),
                "entity_id": ent.id,
                "score": 1.0,
                "hop": 0,
                "source_chunk_id": ent.source_chunk_id,
                "relationships": [
                    {
                        "relationship_id": r.id,
                        "source_entity_id": r.source_entity_id,
                        "target_entity_id": r.target_entity_id,
                        "type": r.relation_type,
                        "summary": r.summary,
                        "source_chunk_id": r.source_chunk_id,
                    }
                    for r in relationships
                ],
            })

            # One-hop expansion by entity_id.
            if hops > 0:
                for r in relationships:
                    other_id = r.target_entity_id if r.source_entity_id == ent.id else r.source_entity_id
                    if other_id not in visited:
                        visited.add(other_id)
                        other_rels = await graph_store.get_relationships_for_entity(other_id)
                        other_ent = await graph_store.get_entity(other_id)
                        output.append({
                            "entity": self._entity_dict(other_ent) if other_ent else {"entity_id": other_id},
                            "entity_id": other_id,
                            "score": 0.0,
                            "hop": 1,
                            "source_chunk_id": other_ent.source_chunk_id if other_ent else "",
                            "relationships": [
                                {
                                    "source_entity_id": or_.source_entity_id,
                                    "target_entity_id": or_.target_entity_id,
                                    "type": or_.relation_type,
                                    "summary": or_.summary,
                                }
                                for or_ in other_rels[:3]
                            ],
                        })

        return output

    @staticmethod
    def _entity_dict(ent: Entity | None) -> dict[str, t.Any]:
        """Convert an Entity to a display dict."""
        if ent is None:
            return {}
        return {
            "entity_id": ent.id,
            "name": ent.name,
            "type": ent.entity_type.value,
            "summary": ent.summary,
            "tags": ent.tags,
            "source_chunk_id": ent.source_chunk_id,
            "source_node_id": ent.source_node_id,
        }


__all__ = [
    "Entity",
    "EntityType",
    "GraphIndexer",
    "GraphStore",
    "Relationship",
]
