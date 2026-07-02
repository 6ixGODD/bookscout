"""Unit tests for derived layer: chunk splitting, entity types, FTS, tag mapping."""

from __future__ import annotations

from bookscout.doccompiler.builder.tagify import tagify_chunk
from bookscout.index.chunk import ChunkIndexer as ChunkBuilder
from bookscout.index.graph import Entity
from bookscout.index.graph import EntityType
from bookscout.index.graph import Relationship

# ----------------------------------------------------------- Entity types


def test_entity_type_from_str_known():
    """EntityType.from_str maps known strings correctly."""
    assert EntityType.from_str("Person") == EntityType.PERSON
    assert EntityType.from_str("person") == EntityType.PERSON
    assert EntityType.from_str("Organization") == EntityType.ORGANIZATION
    assert EntityType.from_str("Concept") == EntityType.CONCEPT


def test_entity_type_from_str_unknown_defaults():
    """EntityType.from_str defaults to Unknown for unrecognized values."""
    assert EntityType.from_str("FooBar") == EntityType.UNKNOWN
    assert EntityType.from_str("") == EntityType.UNKNOWN


def test_entity_type_is_str_enum():
    """EntityType members are strings."""
    assert EntityType.PERSON == "Person"
    assert str(EntityType.PERSON) == "Person"


# ----------------------------------------------------------- Entity/Relationship dataclasses


def test_entity_creation():
    """Entity dataclass holds all fields correctly."""
    ent = Entity(
        id="ent_1",
        book_id="book_1",
        name="Kant",
        entity_type=EntityType.PERSON,
        tags=["philosopher", "历史人物"],
        summary="German philosopher",
        source_chunk_id="chunk_1",
        source_node_id="node_1",
    )
    assert ent.name == "Kant"
    assert ent.entity_type == EntityType.PERSON
    assert "philosopher" in ent.tags
    assert ent.summary == "German philosopher"


def test_relationship_creation():
    """Relationship dataclass holds all fields correctly."""
    rel = Relationship(
        id="rel_1",
        book_id="book_1",
        source_entity_id="ent_1",
        target_entity_id="ent_2",
        relation_type="author_of",
        summary="Kant wrote the Critique",
        claims=["Published in 1781"],
        source_chunk_id="chunk_1",
        source_node_id="node_1",
        content_offset=100,
        content_length=50,
    )
    assert rel.source_entity_id == "ent_1"
    assert rel.target_entity_id == "ent_2"
    assert rel.relation_type == "author_of"


# ----------------------------------------------------------- Chunk splitting


def test_chunk_split_content():
    """ChunkIndexer splits content into correctly-sized chunks."""
    # Use a dummy indexer (we only call _split_by_tokens, not build_index).
    idx = ChunkBuilder.__new__(ChunkBuilder)
    idx._token_budget = 25  # 25 tokens ≈ 100 chars at 4 chars/token
    idx._token_overlap = 5
    idx._estimate_token = None  # Use fallback ~4 chars/token

    content = "A" * 250
    chunks = idx._split_by_tokens(content, base_offset=1000)

    assert len(chunks) >= 2
    # First chunk starts at base_offset.
    assert chunks[0][1] == 1000
    # Chunks cover the full content.
    total_covered = sum(length for _, _, length in chunks)
    assert total_covered >= 250


def test_chunk_split_empty_content():
    """Empty content produces no chunks."""
    idx = ChunkBuilder.__new__(ChunkBuilder)
    idx._token_budget = 100
    idx._token_overlap = 10
    idx._estimate_token = None

    chunks = idx._split_by_tokens("", base_offset=0)
    assert chunks == []


def test_chunk_split_short_content_single_chunk():
    """Content shorter than token budget produces one chunk."""
    idx = ChunkBuilder.__new__(ChunkBuilder)
    idx._token_budget = 500
    idx._token_overlap = 50
    idx._estimate_token = None

    content = "Short text."
    chunks = idx._split_by_tokens(content, base_offset=50)
    assert len(chunks) == 1
    assert chunks[0][0] == "Short text."
    assert chunks[0][1] == 50
    assert chunks[0][2] == len(content)


def test_chunk_does_not_cross_boundaries():
    """Each chunk is within the content boundaries (spec §11.2 #2)."""
    idx = ChunkBuilder.__new__(ChunkBuilder)
    idx._token_budget = 12  # ~48 chars
    idx._token_overlap = 2
    idx._estimate_token = None

    content = "Hello world. " * 20  # ~260 chars
    chunks = idx._split_by_tokens(content, base_offset=100)
    for _, offset, length in chunks:
        assert offset >= 100
        assert offset + length <= 100 + len(content)


# ----------------------------------------------------------- Tagify (already tested, but add one more)


def test_tagify_resolves_range_correctly():
    """Tagify range resolution returns correct absolute offsets."""
    chunk = "Hello.\nWorld!"
    tag_map = tagify_chunk(chunk, chunk_start=200)

    # Tag 0 is at offset 200.
    assert tag_map.resolve_single(0) == 200

    # The last tag is at chunk_end.
    last_tag = max(tag_map.tags.keys())
    assert tag_map.resolve_single(last_tag) == 200 + len(chunk)


# ----------------------------------------------------------- Graph entity merging (via builder helper)


def test_entity_merge_by_name():
    """GraphBuilder._merge_entities_by_name deduplicates by name."""
    from bookscout.index.graph import GraphIndexer
    from bookscout.index.graph import _RawEntity

    builder = GraphIndexer.__new__(GraphIndexer)

    raw_entities = [
        _RawEntity(
            name="Kant",
            entity_type="Person",
            tags=["philosopher"],
            summary="German philosopher",
            source_chunk_id="c1",
            source_node_id="n1",
        ),
        _RawEntity(
            name="kant",
            entity_type="Person",
            tags=["18th century"],
            summary="",
            source_chunk_id="c2",
            source_node_id="n2",
        ),
        _RawEntity(
            name="Hegel",
            entity_type="Person",
            tags=[],
            summary="Another philosopher",
            source_chunk_id="c3",
            source_node_id="n3",
        ),
    ]

    merged = builder._merge_entities_by_name(raw_entities)
    # Kant and kant should merge (case-insensitive).
    assert len(merged) == 2
    kant = next(e for e in merged if e.name.lower() == "kant")
    assert "philosopher" in kant.tags
    assert "18th century" in kant.tags
    assert kant.summary == "German philosopher"  # longer summary kept


def test_cosine_similarity():
    """GraphIndexer._cosine_similarity computes correct values."""
    from bookscout.index.graph import GraphIndexer

    # Identical vectors → 1.0.
    assert abs(GraphIndexer._cosine_similarity([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-6
    # Orthogonal vectors → 0.0.
    assert abs(GraphIndexer._cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-6
    # Empty vectors → 0.0.
    assert GraphIndexer._cosine_similarity([], []) == 0.0
