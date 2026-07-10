"""Tests for IndexProvider + IndexRegistry."""

from __future__ import annotations

import dataclasses
import pathlib

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider
from bookscout.doccompiler.index_registry import IndexRegistry


def _fake_indexer_factory(_ctx: IndexContext):
    return type("FakeIndexer", (), {"index_type": "fake"})()


def _fake_tool_factory(_indexer, _store, _ctx: IndexContext):
    return []


def _fake_store_factory(_ctx: IndexContext):
    return None


def make_provider(t: str, letter: str = "x", default=True, requires_v=False) -> IndexProvider:
    return IndexProvider(
        index_type=t,
        display_name=t.capitalize(),
        short_letter=letter,
        requires_vector_store=requires_v,
        default_enabled=default,
        indexer_factory=_fake_indexer_factory,
        tool_factory=_fake_tool_factory,
        store_factory=_fake_store_factory,
        db_path_name=t,
    )


def test_registry_for_types():
    providers = [make_provider("chunk", "c"), make_provider("summary", "s"), make_provider("graph", "g", default=False)]
    reg = IndexRegistry(providers)
    selected = reg.for_types({"chunk", "graph"})
    types = {p.index_type for p in selected}
    assert types == {"chunk", "graph"}


def test_registry_default_enabled():
    providers = [make_provider("chunk", "c", default=True), make_provider("graph", "g", default=False)]
    reg = IndexRegistry(providers)
    defaults = {p.index_type for p in reg.default_enabled()}
    assert defaults == {"chunk"}


def test_registry_by_type():
    providers = [make_provider("chunk", "c")]
    reg = IndexRegistry(providers)
    assert reg.by_type("chunk") is providers[0]
    assert reg.by_type("missing") is None


def test_registry_letters():
    providers = [make_provider("chunk", "c"), make_provider("summary", "s"), make_provider("graph", "g")]
    reg = IndexRegistry(providers)
    assert reg.letters == "csg"


def test_registry_for_types_filters_unavailable():
    providers = [make_provider("chunk", "c", requires_v=True), make_provider("summary", "s", requires_v=False)]
    reg = IndexRegistry(providers)
    # for_types should not filter by requires_vector_store (that's a caller concern);
    # but we test that for_types returns exactly what's asked.
    selected = reg.for_types({"chunk", "summary"})
    assert {p.index_type for p in selected} == {"chunk", "summary"}


def test_provider_is_frozen():
    p = make_provider("chunk", "c")
    try:
        p.index_type = "x"
        raise AssertionError("should have raised FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_provider_description_defaults_empty():
    p = make_provider("chunk", "c")
    assert p.description == ""


def test_provider_description_field():
    p = IndexProvider(
        index_type="x",
        display_name="X",
        short_letter="x",
        requires_vector_store=False,
        default_enabled=True,
        indexer_factory=_fake_indexer_factory,
        tool_factory=_fake_tool_factory,
        store_factory=_fake_store_factory,
        db_path_name="x",
        description="Some prose",
    )
    assert p.description == "Some prose"


# -- IndexContext tests -------------------------------------------------------


def test_index_context_is_frozen():
    ctx = IndexContext(logger=None, books_store=None)  # type: ignore[arg-type]
    try:
        ctx.logger = "x"  # type: ignore[misc]
        raise AssertionError("should have raised FrozenInstanceError")
    except dataclasses.FrozenInstanceError:
        pass


def test_index_context_optional_fields_default_none():
    ctx = IndexContext(logger=None, books_store=None)  # type: ignore[arg-type]
    assert ctx.llm is None
    assert ctx.embedding is None
    assert ctx.vector_store is None
    assert ctx.db_path is None


def test_index_context_all_fields():
    ctx = IndexContext(
        logger="log",  # type: ignore[arg-type]
        books_store="bs",  # type: ignore[arg-type]
        llm="chat",  # type: ignore[arg-type]
        embedding="emb",  # type: ignore[arg-type]
        vector_store="vs",  # type: ignore[arg-type]
        db_path=pathlib.Path("/tmp/x.sqlite"),
    )
    assert ctx.logger == "log"
    assert ctx.books_store == "bs"
    assert ctx.llm == "chat"
    assert ctx.embedding == "emb"
    assert ctx.vector_store == "vs"
    assert ctx.db_path == pathlib.Path("/tmp/x.sqlite")


# -- Real provider tests -----------------------------------------------------


def test_summary_provider_factory_signatures():
    """Summary provider factories should accept IndexContext-based signatures."""
    # Verify factories are callable and accept the new signatures.
    # We use lightweight mocks to avoid requiring real Logger/BooksStore.
    import inspect

    from bookscout.index.summary.provider import INDEX_PROVIDER as SUMMARY_PROVIDER

    sig_indexer = inspect.signature(SUMMARY_PROVIDER.indexer_factory)
    assert "ctx" in sig_indexer.parameters

    sig_store = inspect.signature(SUMMARY_PROVIDER.store_factory)
    assert "ctx" in sig_store.parameters

    sig_tool = inspect.signature(SUMMARY_PROVIDER.tool_factory)
    assert "ctx" in sig_tool.parameters
    assert "indexer" in sig_tool.parameters
    assert "store" in sig_tool.parameters


def test_chunk_provider_factory_signatures():
    """Chunk provider factories should accept IndexContext-based signatures."""
    import inspect

    from bookscout.index.chunk.provider import INDEX_PROVIDER as CHUNK_PROVIDER

    sig_indexer = inspect.signature(CHUNK_PROVIDER.indexer_factory)
    assert "ctx" in sig_indexer.parameters

    sig_store = inspect.signature(CHUNK_PROVIDER.store_factory)
    assert "ctx" in sig_store.parameters

    sig_tool = inspect.signature(CHUNK_PROVIDER.tool_factory)
    assert "ctx" in sig_tool.parameters
    assert "indexer" in sig_tool.parameters
    assert "store" in sig_tool.parameters


def test_graph_provider_factory_signatures():
    """Graph provider factories should accept IndexContext-based signatures."""
    import inspect

    from bookscout.index.graph.provider import INDEX_PROVIDER as GRAPH_PROVIDER

    sig_indexer = inspect.signature(GRAPH_PROVIDER.indexer_factory)
    assert "ctx" in sig_indexer.parameters

    sig_store = inspect.signature(GRAPH_PROVIDER.store_factory)
    assert "ctx" in sig_store.parameters

    sig_tool = inspect.signature(GRAPH_PROVIDER.tool_factory)
    assert "ctx" in sig_tool.parameters
    assert "indexer" in sig_tool.parameters
    assert "store" in sig_tool.parameters
