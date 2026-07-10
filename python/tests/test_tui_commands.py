"""Tests for the BookScoutTui command system and index_select UI.

These tests drive the TUI headlessly via ``run_test``. The heavy ``ReplContext.startup``
is bypassed by injecting a ``_FakeReplContext`` directly into the app after mount,
so the test is fast and does not require environment API keys.
"""

from __future__ import annotations

import contextlib
import pathlib
import tempfile

from textual.widgets import Input

from bookscout.books import Book
from bookscout.doccompiler.index_provider import IndexContext
from bookscout.doccompiler.index_provider import IndexProvider
from bookscout.doccompiler.index_registry import IndexRegistry
from bookscout.repl.config import BookScoutConfig
from bookscout.repl.tui import BookScoutTui

# -- Fakes -------------------------------------------------------------------


def _fake_indexer_factory(_ctx: IndexContext):
    return type("FakeIndexer", (), {"index_type": "fake"})()


def _fake_tool_factory(_indexer, _store, _ctx: IndexContext):
    return []


def _fake_store_factory(_ctx: IndexContext):
    return None


def make_provider(
    t: str,
    letter: str = "x",
    default: bool = True,
    requires_v: bool = False,
    desc: str = "",
) -> IndexProvider:
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
        description=desc,
    )


def _registry() -> IndexRegistry:
    return IndexRegistry([
        make_provider("chunk", "c", default=True, desc="Chunk description"),
        make_provider("summary", "s", default=True, desc="Summary description"),
        make_provider("graph", "g", default=False, desc="Graph description"),
    ])


class _FakeReplContext:
    """Bare-bones context that satisfies BookScoutTui's needs in tests."""

    default_builder = "rule"
    has_llm_builder = False

    def __init__(self, books: list[Book]) -> None:
        self._books = books
        self._registry = _registry()
        self.has_chat = False  # so :book N won't enter chat (just sets status)

    @property
    def registry(self) -> IndexRegistry:
        return self._registry

    @property
    def data_dir(self) -> pathlib.Path:
        return pathlib.Path()

    async def list_books(self) -> list[Book]:
        return list(self._books)

    async def shutdown(self) -> None:
        pass


# -- Driver helper -----------------------------------------------------------


@contextlib.asynccontextmanager
async def drive(app: BookScoutTui, *, books: list[Book] | None = None):
    """Mount ``app`` via ``run_test``, inject a fake ctx, enter select phase,
    yield the pilot.

    Bypasses the heavy ``ReplContext.startup`` worker by stubbing
    ``BookScoutTui._startup`` on the instance to a fast no-op before mount.

    Also suppresses the production ``os._exit(0)`` in ``on_unmount`` so the test
    runner isn't killed when the headless app exits.
    """
    if books is None:
        books = [
            Book.new(book_id="b1", title="First", author="AuthA"),
            Book.new(book_id="b2", title="Second", author="AuthB"),
        ]

    fake_ctx = _FakeReplContext(books)
    app._repl_context = fake_ctx  # type: ignore[attr-defined]
    app._books = list(books)

    async def _stub_startup() -> None:
        return

    app._startup = _stub_startup  # type: ignore[method-assign]

    # BookScoutTui.on_unmount calls ``os._exit(0)`` to kill the live REPL —
    # in tests that's catastrophic for the pytest worker. Patch it out for the
    # duration of run_test. Touching the classmethod is necessary because
    # Textual's message handler dispatch resolves on_unmount via type(self),
    # not the instance dict.
    original_on_unmount = BookScoutTui.on_unmount

    async def _stub_on_unmount(self: BookScoutTui) -> None:  # noqa: ARG001
        return

    BookScoutTui.on_unmount = _stub_on_unmount  # type: ignore[method-assign, attr-defined]

    try:
        async with app.run_test(size=(120, 36)) as pilot:
            app._books = list(books)
            app._refresh_books_list()
            app.phase = "select"
            app._set_status(f"  {len(books)} book(s)")
            app._focus_input()
            await pilot.pause()
            yield pilot
    finally:
        BookScoutTui.on_unmount = original_on_unmount  # type: ignore[method-assign, attr-defined]


def _static_text(widget_id: str, app: BookScoutTui) -> str:
    """Read a Static widget's current content (Textual 8.x)."""
    w = app.query_one(widget_id)
    # Name-mangled attribute holding the str passed to the constructor/update.
    return str(getattr(w, "_Static__content", "")) or ""


def _status(app: BookScoutTui) -> str:
    return _static_text("#status_bar", app)


def _select_input(app: BookScoutTui) -> Input:
    return app.query_one("#select_input", Input)


def _chat_input(app: BookScoutTui) -> Input:
    return app.query_one("#chat_input", Input)


# -- Tests: select phase -----------------------------------------------------


async def test_select_rejects_non_command() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        _select_input(app).value = "hello"
        await pilot.press("enter")
        await pilot.pause()
        assert "Unknown command" in _status(app)
        assert app.phase == "select"


async def test_select_unknown_command() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        _select_input(app).value = ":foo"
        await pilot.press("enter")
        await pilot.pause()
        assert "Unknown command: :foo" in _status(app)


async def test_select_book_multi_rejected() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        _select_input(app).value = ":book 1,2"
        await pilot.press("enter")
        await pilot.pause()
        assert "multi-select" in _status(app)


async def test_select_book_out_of_range() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        _select_input(app).value = ":book 999"
        await pilot.press("enter")
        await pilot.pause()
        assert "no book" in _status(app)


async def test_select_book_no_arg() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        _select_input(app).value = ":book"
        await pilot.press("enter")  # palette selects "book" → pastes ":book "
        await pilot.pause()
        await pilot.press("enter")  # execute
        await pilot.pause()
        assert "usage" in _status(app).lower()


async def test_select_compile_no_arg() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        _select_input(app).value = ":compile"
        await pilot.press("enter")  # palette selects → pastes ":compile "
        await pilot.pause()
        await pilot.press("enter")  # execute
        await pilot.pause()
        assert "usage" in _status(app).lower()
        assert app.phase == "select"


# -- Tests: index_select phase -----------------------------------------------


async def test_select_compile_enters_index_select() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            assert app.phase == "index_select"
            assert app._compile_source == str(tmp)
            # Default selection: chunk + summary on, graph off.
            assert app._selected_index_types == {"chunk", "summary"}
            assert app._index_focus_idx == 0
    finally:
        tmp.unlink(missing_ok=True)


async def test_index_select_arrow_keys_move_focus() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            assert app._index_focus_idx == 0
            await pilot.press("down")
            await pilot.pause()
            assert app._index_focus_idx == 1
            await pilot.press("down")
            await pilot.pause()
            assert app._index_focus_idx == 2
            # Wrap-around (3 rows: chunk, summary, graph).
            await pilot.press("down")
            await pilot.pause()
            assert app._index_focus_idx == 0
            await pilot.press("up")
            await pilot.pause()
            assert app._index_focus_idx == 2
    finally:
        tmp.unlink(missing_ok=True)


async def test_index_select_space_toggles_focused() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            # Focus on chunk (default idx 0), Space toggles it off.
            assert "chunk" in app._selected_index_types
            await pilot.press("space")
            await pilot.pause()
            assert "chunk" not in app._selected_index_types
            # Move down to summary, toggle off.
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            assert "summary" not in app._selected_index_types
            # Move down to graph (off by default), toggle on.
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            assert app._selected_index_types == {"graph"}
    finally:
        tmp.unlink(missing_ok=True)


async def test_index_select_back_returns_to_select() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            _select_input(app).value = ":back"
            await pilot.press("enter")  # palette selects "back" → pastes ":back "
            await pilot.pause()
            await pilot.press("enter")  # execute
            await pilot.pause()
            assert app.phase == "select"
    finally:
        tmp.unlink(missing_ok=True)


async def test_index_select_non_command_rejected() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            _select_input(app).value = "garbage"
            await pilot.press("enter")
            await pilot.pause()
            assert "Unknown command (commands start with `:`)" in _status(app)
    finally:
        tmp.unlink(missing_ok=True)


async def test_index_select_unknown_command() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            _select_input(app).value = ":foo"
            await pilot.press("enter")
            await pilot.pause()
            assert "Unknown command: :foo" in _status(app)
    finally:
        tmp.unlink(missing_ok=True)


async def test_index_select_quit_command() -> None:
    tmp = pathlib.Path(tempfile.gettempdir()) / "demo.epub"
    tmp.touch()
    try:
        app = BookScoutTui(BookScoutConfig())
        async with drive(app) as pilot:
            _select_input(app).value = f":compile {tmp}"
            await pilot.press("enter")
            await pilot.pause()
            # Trigger :quit — Textual will exit the run_test loop.
            _select_input(app).value = ":quit"
            await pilot.press("enter")
            await pilot.pause()
            # After exit, querying widgets may raise; just assert phase didn't
            # switch to compile inadvertently.
            assert app.phase in ("index_select",)  # did not start a compile
    finally:
        tmp.unlink(missing_ok=True)


# -- Tests: chat phase -------------------------------------------------------


async def test_chat_path_no_longer_triggers_compile() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        # Force into chat phase with a selected book (skip _enter_chat guards).
        app._selected_book = Book.new(book_id="b1", title="First", author="AuthA")
        app._session_id = "test_sess"
        app.phase = "chat"
        await pilot.pause()
        _chat_input(app).value = "my.epub"
        await pilot.press("enter")
        await pilot.pause()
        assert app.phase == "chat"


async def test_chat_unknown_command_rejected() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as pilot:
        app._selected_book = Book.new(book_id="b1", title="First", author="AuthA")
        app.phase = "chat"
        await pilot.pause()
        _chat_input(app).value = ":foo"
        await pilot.press("enter")
        await pilot.pause()
        assert "Unknown chat command: :foo" in _status(app)


# -- Tests: input starts empty (no prompt prefix baked in) -------------------


async def test_input_is_plain_no_prefix() -> None:
    app = BookScoutTui(BookScoutConfig())
    async with drive(app) as _pilot:
        si = _select_input(app)
        assert si.value == ""
        assert not si.placeholder


# -- Tests: config defaults ---------------------------------------------------


def test_config_workdir_default() -> None:
    config = BookScoutConfig()
    assert config.workdir == str(pathlib.Path.home() / ".bookscout")
    assert config.mcp_servers == []
    assert config.skills == []


def test_config_resolved_data_dir() -> None:
    config = BookScoutConfig(workdir="/tmp/test_bs")
    assert str(config.resolved_data_dir) == str(pathlib.Path("/tmp/test_bs/data"))
