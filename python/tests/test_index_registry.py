"""Tests for IndexProvider + IndexRegistry."""

from __future__ import annotations

import dataclasses

from bookscout.doccompiler.index_provider import IndexProvider
from bookscout.doccompiler.index_registry import IndexRegistry


def _fake_indexer_factory(_logger, _books_store, **_kw):
    return type("FakeIndexer", (), {"index_type": "fake"})()


def _fake_tool_factory(_indexer, _store, **_kw):
    return []


def _fake_store_factory(_db_path, _logger, **_kw):
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
