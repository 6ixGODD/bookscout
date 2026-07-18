"""BookScout TUI 閳?vim-like minimal terminal UI over ReplContext.

Layout::

    Header (phase-specific hint)
    --- (white bold line)
    Content area  (book list / markdown chat / compile log / index select)
    --- (white bold line)
    Input
    --- (white bold line)
    Status

Pure black background. No borders. Focus always on input.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import typing as t

from rich.text import Text
from textual import events
from textual import on
from textual.app import App
from textual.app import ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Input
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import Markdown
from textual.widgets import RichLog
from textual.widgets import Rule
from textual.widgets import Static

from .config import BookScoutConfig
from .context import ReplContext

if t.TYPE_CHECKING:
    from bookscout.agents.mode import StreamChunk
    from bookscout.books import Book
    from bookscout.doccompiler.task_manager import TaskProgress
    from bookscout.repl.session_manager import Session


class CommandInput(Input):
    """A plain :class:`Input` with arrow-key / Space / colon-palette delegation."""

    async def _on_key(self, event: events.Key) -> None:
        app = self.app
        phase = getattr(app, "phase", "")
        palette = getattr(app, "_palette_open", False)

        if palette:
            if event.key in ("escape", "up", "down", "enter"):
                event.stop()
                event.prevent_default()
                if event.key == "escape":
                    app._close_palette()  # type: ignore[attr-defined]
                elif event.key == "up":
                    app._palette_move(-1)  # type: ignore[attr-defined]
                elif event.key == "down":
                    app._palette_move(1)  # type: ignore[attr-defined]
                elif event.key == "enter":
                    app._accept_palette()  # type: ignore[attr-defined]
                return
            # Backspace / printable: let the Input handle them normally,
            # then re-render the palette from the updated input value.
            await super()._on_key(event)
            app._render_palette()  # type: ignore[attr-defined]
            return

        if phase in ("session_select",) and event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            if event.key == "up":
                app._session_focus_idx = max(0, (app._session_focus_idx or 0) - 1)
            elif event.key == "down":
                n = len(getattr(app, "_session_list", []))
                app._session_focus_idx = min(n - 1, (app._session_focus_idx or 0) + 1) if n else 0
            if getattr(app, "_session_select_cross_book", False):
                await app._render_cross_book_session_list()  # type: ignore[attr-defined]
            else:
                await app._render_session_list()  # type: ignore[attr-defined]
            return

        if phase in ("select",) and event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            if event.key == "up":
                app._book_focus_idx = max(0, (app._book_focus_idx or 0) - 1)
            elif event.key == "down":
                n = len(getattr(app, "_books", []))
                app._book_focus_idx = min(n - 1, (app._book_focus_idx or 0) + 1) if n else 0
            await app._refresh_books_list()  # type: ignore[attr-defined]
            return

        if phase in ("index_select", "builder_select") and event.key in ("up", "down", "space"):
            event.stop()
            event.prevent_default()
            if event.key == "up":
                app._move_index_focus(-1)  # type: ignore[attr-defined]
            elif event.key == "down":
                app._move_index_focus(+1)  # type: ignore[attr-defined]
            elif event.key == "space":
                app._toggle_index_focus()  # type: ignore[attr-defined]
            return
        await super()._on_key(event)

    def on_input_changed(self, event: Input.Changed) -> None:
        """Detect colon-command mode from input value."""
        app = self.app
        if getattr(app, "_skip_palette", False):
            return
        value = event.value
        if value.startswith(":") and " " not in value:
            app._open_palette()  # type: ignore[attr-defined]
        elif getattr(app, "_palette_open", False):
            app._close_palette()  # type: ignore[attr-defined]


class BookScoutTui(App[None]):
    """The BookScout terminal UI."""

    CSS = """
    $surface: transparent;
    $panel: transparent;
    $boost: #111111;
    $text-muted: #999999;
    Screen {
        background: transparent;
        color: #c0c0c0;
        scrollbar-size: 0 0;
        layers: none;
        overflow: hidden hidden;
        width: 100%;
        height: 100%;
    }
    * {
        scrollbar-size: 0 0;
        margin: 0;
        padding: 0;
    }
    #status_bar {
        dock: bottom;
        height: 1;
        color: #666666;
        padding: 0 0 0 2;
    }
    #header {
        layout: vertical;
        height: auto;
        padding: 0 0 0 2;
    }
    #header_brand {
        color: #ffffff;
        text-style: bold;
        height: 1;
    }
    #header_hint {
        color: #555555;
        height: auto;
        min-height: 1;
    }
    #header_rule {
        color: #ffffff;
        height: 1;
    }
    #main {
        layout: vertical;
        width: 100%;
        height: 1fr;
        padding: 0 0 0 2;
    }
    .log-area {
        height: 1fr;
        scrollbar-size: 0 0;
    }
    #chat_log {
        overflow-y: auto;
        scrollbar-size: 0 0;
    }
    #index_select_hint, #builder_select_hint {
        color: #c0c0c0;
        height: 1;
    }
    #spinner_line, #chat_spinner_line {
        height: 1;
        color: #666666;
        padding: 0;
    }
    #input_area {
        height: auto;
        min-height: 3;
        padding: 0;
    }
    #select_input, #chat_input {
        width: 100%;
    }
    #error_display {
        color: #cc6666;
        padding: 0 2;
        height: auto;
        max-height: 6;
    }
    #command_palette {
        background: $boost;
        border: none;
        height: auto;
        max-height: 14;
        margin: 0 2;
        display: none;
        scrollbar-size: 0 0;
    }
    #palette_list {
        height: auto;
    }
    Container {
        background: transparent;
        width: 100%;
    }
    Rule {
        color: #ffffff;
        background: transparent;
    }
    Input {
        border-top: solid #ffffff;
        border-bottom: solid #ffffff;
        border-left: none;
        border-right: none;
        background: transparent;
        color: #c0c0c0;
        padding: 0 0 0 2;
        height: 3;
        width: 100%;
    }
    Input:focus {
        border-top: solid #ffffff;
        border-bottom: solid #ffffff;
    }
    ListView > ListItem {
        padding: 0;
        background: transparent;
    }
    ListView:focus > ListItem.--highlight {
        background: #333333;
    }
    ProgressBar {
        background: #333333;
    }
    """

    BINDINGS = [
        ("ctrl+c", "handle_ctrl_c", "Quit / Copy"),
        ("ctrl+o", "toggle_verbose_tools", "Toggle verbose tool calls"),
    ]

    phase: reactive[str] = reactive("init", layout=True)

    def __init__(
        self,
        config: BookScoutConfig,
        *,
        initial_book_id: str | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._initial_book_id = initial_book_id
        self._resume_session_id = resume_session_id
        self._repl_context: ReplContext | None = None
        self._books: list[Book] = []
        self._selected_book: Book | None = None
        self._pending_task_id: str | None = None
        self._progress_timer: t.Any = None
        self._streaming_buffer: list[str] = []
        self._streaming_started = False
        self._chat_busy = False
        self._spinner_frames = ["|", "/", "-", "\\"]
        self._spinner_idx = 0
        self._spinner_timer: t.Any = None
        self._spinner_active = False
        self._compile_source = ""
        self._selected_index_types: set[str] = set()
        self._index_focus_idx: int = 0
        self._selected_builder: str = "rule"
        self._verbose_tools: bool = False
        self._chat_markdown: str = ""
        self._post_compile_target = "select"
        self._palette_open = False
        self._session_id: str | None = None
        self._session_list: list[Session] = []
        self._session_focus_idx: int = 0
        self._current_session: Session | None = None
        self._session_select_cross_book: bool = False
        self._book_focus_idx: int = 0

    def compose(self) -> ComposeResult:
        with Container(id="header"):
            yield Static("BookScout", id="header_brand")
            yield Static("", id="header_hint")
            yield Rule(id="header_rule")
        with Container(id="main"):
            # Select panel
            with Container(id="select_panel"):
                yield ListView(id="books_list", classes="log-area")
                yield Static("", id="error_display")
            # Index select panel (shown between select and compile).
            with Container(id="index_select_panel"):
                yield Static("Indexes to build:", id="index_select_hint")
                yield Static("", id="index_select_list", classes="log-area")
                yield Static("", id="index_select_error")
            # Builder select panel (shown after index selection).
            with Container(id="builder_select_panel"):
                yield Static("Builder mode:", id="builder_select_hint")
                yield Static("", id="builder_select_list", classes="log-area")
            # Compile panel
            with Container(id="compile_panel"):
                yield RichLog(id="compile_log", markup=True, wrap=True, classes="log-area")
                yield Static("", id="spinner_line")
            # Chat panel
            with Container(id="chat_panel"):
                yield Markdown(id="chat_log", classes="log-area")
                yield Static("", id="chat_spinner_line")
        with Container(id="input_area"):
            yield CommandInput(id="select_input")
            yield CommandInput(id="chat_input")
        with Container(id="command_palette"):
            yield Static("", id="palette_list")
        yield Static("", id="status_bar")

    def on_mount(self) -> None:
        self._set_panel("init")
        self.run_worker(self._startup, exclusive=True, group="startup")  # type: ignore[arg-type]

    async def on_unmount(self) -> None:
        if self._repl_context is not None:
            import asyncio

            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._repl_context.shutdown(), timeout=1.0)
        os._exit(0)

    # -- Startup --
    async def _startup(self) -> None:
        self._set_status("  starting...")
        try:
            self._repl_context = ReplContext(self._config)
            await self._repl_context.startup()
        except Exception as e:
            self._set_status(f"  startup failed: {e}")
            self.phase = "error"
            return

        ctx = self._repl_context
        assert ctx is not None
        self._books = await ctx.list_books()

        if self._resume_session_id:
            session = await ctx.session_manager.get(self._resume_session_id)
            if session is not None:
                book = next((b for b in self._books if b.id == session.book_id), None)
                if book is not None:
                    self._selected_book = book
                    self._session_id = session.session_id
                    self._current_session = session
                    await self._enter_chat_with_session(book, session)
                    return
            self._set_status(f"  session not found: {self._resume_session_id}")

        if self._initial_book_id:
            book = next((b for b in self._books if b.id == self._initial_book_id), None)
            if book is not None:
                await self._enter_chat(book)  # handles session creation
                return

        await self._refresh_books_list()
        self.phase = "select"
        self._set_status(f"  {len(self._books)} book(s)" + ("" if ctx.has_chat else "  [no LLM/embedding]"))
        self._focus_input()

    # -- Phase switching --
    def watch_phase(self, phase: str) -> None:
        self._set_panel(phase)
        self._update_header_hint(phase)
        if phase == "compile":
            self._start_progress_polling()
        else:
            self._stop_progress_polling()
        self._focus_input()

    def _focus_input(self) -> None:
        """Keep focus on the input box at all times."""
        with contextlib.suppress(Exception):
            if self.phase in ("select", "index_select", "builder_select", "session_select"):
                self.query_one("#select_input", Input).focus()
            elif self.phase == "chat":
                self.query_one("#chat_input", Input).focus()

    @staticmethod
    def _header_hint_for_phase(phase: str) -> str:
        if phase == "select":
            return "↑↓ select    Enter: open    : for commands"
        if phase == "session_select":
            return "type : for commands    Enter: resume    :new: create    :back: return to books"
        if phase == "index_select":
            return "type : for commands    Space/Enter  toggle    Enter/:go  next"
        if phase == "builder_select":
            return "type : for commands    Space/Enter  pick    Enter/:go  build"
        if phase == "compile":
            return ""
        if phase == "chat":
            return "type : for commands"
        return ""

    @staticmethod
    def _set_console_title(title: str) -> None:
        """Set the terminal window/tab title via escape sequence."""
        import sys

        sys.stdout.write(f"\x1b]0;{title}\x07")
        sys.stdout.flush()

    def _update_header_hint(self, phase: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#header_hint", Static).update(self._header_hint_for_phase(phase))

    def _set_panel(self, phase: str) -> None:
        panel_map = {
            "select": "select_panel",
            "session_select": "select_panel",
            "index_select": "index_select_panel",
            "builder_select": "builder_select_panel",
            "compile": "compile_panel",
            "chat": "chat_panel",
        }
        active = panel_map.get(phase, "")
        for panel_id in (
            "select_panel",
            "index_select_panel",
            "builder_select_panel",
            "compile_panel",
            "chat_panel",
        ):
            with contextlib.suppress(Exception):
                self.query_one(f"#{panel_id}", Container).display = panel_id == active
        # Show the right input.
        with contextlib.suppress(Exception):
            self.query_one("#select_input", Input).display = phase in (
                "select",
                "session_select",
                "index_select",
                "builder_select",
            )
            self.query_one("#chat_input", Input).display = phase in ("chat", "compile")

    def _set_status(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#status_bar", Static).update(text)

    def _show_error(self, message: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#error_display", Static).update(message)

    def _clear_error(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#error_display", Static).update("")

    async def _refresh_books_list(self) -> None:
        lv = self.query_one("#books_list", ListView)
        await lv.clear()
        registry = self._repl_context.registry if self._repl_context else None
        for idx, book in enumerate(self._books):
            title = book.title or "(untitled)"
            author = book.author or "Unknown"
            focused = idx == self._book_focus_idx
            style = "bold white" if focused else "#888888"
            dim_style = "#666666" if focused else "#444444"
            flags: list[Text] = []
            if registry is not None:
                built = set(book.indexes)
                for provider in registry.all():
                    mark = "\u221a" if provider.index_type in built else "\u00d7"
                    flag_style = "bold white" if provider.index_type in built else dim_style
                    flags.append(Text(f" {mark} {provider.display_name} ", style=flag_style))
            else:
                built_count = len(book.indexes) if book.indexes else 0
                flags.append(Text(f" {built_count} idx", style=dim_style))

            label = Text.assemble(
                Text(f"  {idx + 1:>2}  ", style=style),
                Text(title, style=style),
                Text(f"  {author}", style=dim_style),
                Text("  "),
                *flags,
            )
            lv.append(ListItem(Static(label)))

    # -- Spinner --
    def _start_spinner(self, msg: str = "") -> None:
        self._spinner_active = True
        self._spinner_msg = msg
        self._spinner_idx = 0
        self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def _stop_spinner(self) -> None:
        self._spinner_active = False
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        spinner_id = "#chat_spinner_line" if self.phase == "chat" else "#spinner_line"
        with contextlib.suppress(Exception):
            self.query_one(spinner_id, Static).update("")

    def _tick_spinner(self) -> None:
        if not self._spinner_active:
            return
        frame = self._spinner_frames[self._spinner_idx % len(self._spinner_frames)]
        self._spinner_idx += 1
        msg = f"  {frame} {self._spinner_msg}" if self._spinner_msg else f"  {frame}"
        spinner_id = "#chat_spinner_line" if self.phase == "chat" else "#spinner_line"
        with contextlib.suppress(Exception):
            self.query_one(spinner_id, Static).update(msg)

    # -- Select phase --
    @on(ListView.Selected)
    async def _book_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "books_list":
            return
        idx = event.list_view.index
        if idx is None or idx >= len(self._books):
            return
        await self._enter_chat(self._books[idx])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self._skip_palette = False
        if event.input.id == "select_input":
            if self.phase == "session_select":
                await self._handle_session_select_input(event.value.strip())
            elif self.phase == "index_select":
                self._handle_index_select_input(event.value.strip())
            elif self.phase == "builder_select":
                self._handle_builder_select_input(event.value.strip())
            else:
                await self._handle_select_input(event.value.strip())
        elif event.input.id == "chat_input":
            await self._handle_chat_input(event.value.strip())

    @staticmethod
    def _clean_path(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value.strip()

    async def _handle_select_input(self, value: str) -> None:
        # Enter on empty input → select the focused book.
        if not value:
            if self._books and 0 <= self._book_focus_idx < len(self._books):
                await self._enter_chat(self._books[self._book_focus_idx])
            return
        self.query_one("#select_input", Input).value = ""
        if not value.startswith(":"):
            self._set_status("  Unknown command (commands start with `:`)")
            return
        low = value.lower()
        if low in (":q", ":quit", ":exit"):
            self.exit()
            return
        if low == ":back":
            self._set_status("  already at book list")
            return
        if low == ":resume":
            mgr = self._repl_context.session_manager
            all_sessions = await mgr.list_all()
            if not all_sessions:
                self._set_status("  no sessions to resume")
                return
            self._session_list = all_sessions
            self._session_focus_idx = 0
            self._session_select_cross_book = True
            await self._render_cross_book_session_list()
            self.phase = "session_select"
            self._set_status(f"  {len(all_sessions)} session(s) total")
            return
        # :compile <path> — add a new book.
        if low.startswith(":compile") or low.startswith(":c "):
            parts = value.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                self._set_status("  usage: :compile <path>")
                return
            if self._repl_context is None:
                return
            path = self._clean_path(parts[1])
            self._clear_error()
            self._enter_index_select(path)
            return
        # :addindex N <type> 閳?add an index to an existing book.
        if low.startswith(":addindex ") or low.startswith(":addidx "):
            parts = value.split()
            if len(parts) < 3 or not parts[1].isdigit():
                self._set_status("  usage: :addindex N <type>")
                return
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(self._books)):
                self._set_status(f"  no book #{parts[1]}")
                return
            book = self._books[idx]
            idx_type = parts[2].lower()
            if self._repl_context is None:
                return
            provider = self._repl_context.registry.by_type(idx_type)
            if provider is None:
                self._set_status(f"  unknown index: {idx_type}")
                return
            if idx_type in set(book.indexes):
                self._set_status(f"  {idx_type} already built for #{parts[1]}")
                return
            self.run_worker(
                self._start_add_index(book.id, {idx_type}, post_target="select"),
                exclusive=True,
                group="compile",
            )  # type: ignore[arg-type]
            return
        # :rmindex N <type> 閳?remove an index from an existing book.
        if low.startswith(":rmindex ") or low.startswith(":rmidx "):
            parts = value.split()
            if len(parts) < 3 or not parts[1].isdigit():
                self._set_status("  usage: :rmindex N <type>")
                return
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(self._books)):
                self._set_status(f"  no book #{parts[1]}")
                return
            book = self._books[idx]
            idx_type = parts[2].lower()
            if idx_type not in set(book.indexes):
                self._set_status(f"  {idx_type} not built for #{parts[1]}")
                return
            self.run_worker(
                self._do_rm_index(book.id, idx_type),
                exclusive=True,
                group="compile",
            )  # type: ignore[arg-type]
            return
        # :delete N 閳?remove a book and its workspace.
        if low.startswith(":delete") or low.startswith(":del"):
            parts = value.split()
            if len(parts) < 2 or not parts[1].isdigit():
                self._set_status("  usage: :delete N")
                return
            idx = int(parts[1]) - 1
            if 0 <= idx < len(self._books):
                book = self._books[idx]
                self.run_worker(self._delete_book(book), exclusive=True, group="delete")  # type: ignore[arg-type]
            else:
                self._set_status(f"  no book #{parts[1]}")
            return
        self._set_status(f"  Unknown command: {value}")

    def _enter_index_select(self, source_path: str) -> None:
        """Enter the index-select phase for a new compile."""
        assert self._repl_context is not None
        self._compile_source = source_path
        if self._repl_context.registry is not None:
            self._selected_index_types = {p.index_type for p in self._repl_context.registry.default_enabled()}
        else:
            self._selected_index_types = set()
        self._index_focus_idx = 0
        self._render_index_select()
        self.phase = "index_select"
        self._set_status(f"  select indexes for: {pathlib.Path(source_path).name}")
        self._focus_input()

    def _index_select_rows(self) -> list[tuple[str, str, str]]:
        """Return index provider rows for the list."""
        assert self._repl_context is not None
        return [
            (f"index:{p.index_type}", p.display_name, p.description or "") for p in self._repl_context.registry.all()
        ]

    def _render_index_select(self) -> None:
        """Render the index selection list (indexes only)."""
        assert self._repl_context is not None
        rows = self._index_select_rows()
        name_w = max((len(label) for _key, label, _desc in rows), default=0)
        out = Text()
        for idx, (key, label, desc) in enumerate(rows):
            if idx > 0:
                out.append(Text("\n"))
            focused = idx == self._index_focus_idx
            style = "bold white" if focused else "#888888"
            desc_style = "#cccccc" if focused else "#444444"
            index_type = key.split(":", 1)[1]
            checked = index_type in self._selected_index_types
            box = "[*]" if checked else "[ ]"
            out.append(Text(f"  {box}  ", style=style))
            out.append(Text(label.ljust(name_w), style=style))
            if desc:
                out.append(Text("    "))
                out.append(Text(desc, style=desc_style))
        out.append(Text("\n\n"))
        out.append(
            Text(
                "  up/down  focus    Space  toggle    Enter/:go  next    :back  cancel",
                style="#666666",
            )
        )
        with contextlib.suppress(Exception):
            self.query_one("#index_select_list", Static).update(out)
        names = ", ".join(sorted(self._selected_index_types)) or "none"
        self._set_status(f"  indexes: {names}")

    def _move_index_focus(self, delta: int) -> None:
        """Move the index selection focus by ``delta`` (clamped to row range)."""
        if self.phase == "builder_select":
            rows = self._builder_rows()
        else:
            assert self._repl_context is not None
            rows = self._index_select_rows()
        if not rows:
            return
        n = len(rows)
        self._index_focus_idx = (self._index_focus_idx + delta) % n
        if self.phase == "builder_select":
            self._render_builder_select()
        else:
            self._render_index_select()

    def _toggle_index_focus(self) -> None:
        """Act on the currently focused row 閳?toggle index / pick builder."""
        if self.phase == "builder_select":
            rows = self._builder_rows()
            if not rows:
                return
            key, _label, _desc = rows[self._index_focus_idx]
            builder_key = key.split(":", 1)[1]
            self._selected_builder = builder_key
            self._render_builder_select()
            self._set_status(f"  builder: {builder_key}")
            return

        assert self._repl_context is not None
        rows = self._index_select_rows()
        if not rows:
            return
        key, _label, _desc = rows[self._index_focus_idx]
        if key.startswith("index:"):
            index_type = key.split(":", 1)[1]
            if index_type in self._selected_index_types:
                self._selected_index_types.discard(index_type)
            else:
                self._selected_index_types.add(index_type)
            self._render_index_select()

    def _handle_index_select_input(self, text: str) -> None:
        """Handle input in the index_select phase."""
        self.query_one("#select_input", Input).value = ""
        low = text.lower().strip()

        if low == "" or low in (":go", ":ok", ":next"):
            if self._selected_index_types:
                self._enter_builder_select()
            else:
                self._set_status("  select at least one index")
            return

        if not low.startswith(":"):
            self._set_status("  Unknown command (commands start with `:`)")
            return

        if low in (":back", ":cancel", ":select"):
            self.phase = "select"
            self._set_status(f"  {len(self._books)} book(s)")
            return

        if low in (":q", ":quit", ":exit"):
            self.exit()
            return

        self._set_status(f"  Unknown command: {text}")

    # -- Builder select phase --
    def _enter_builder_select(self) -> None:
        """Enter the builder-select phase after indexes are chosen."""
        assert self._repl_context is not None
        self._selected_builder = self._repl_context.default_builder
        self._index_focus_idx = 0
        self._render_builder_select()
        self.phase = "builder_select"
        self._set_status(f"  builder: {self._selected_builder}")
        self._focus_input()

    def _builder_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = [
            ("builder:rule", "Rule", "Fast, deterministic heuristics (default)"),
        ]
        assert self._repl_context is not None
        if self._repl_context.has_llm_builder:
            rows.append(("builder:llm", "LLM", "Tool-driven outline construction; slower, higher quality"))
        return rows

    def _render_builder_select(self) -> None:
        rows = self._builder_rows()
        name_w = max((len(label) for _key, label, _desc in rows), default=0)
        out = Text()
        for idx, (key, label, desc) in enumerate(rows):
            if idx > 0:
                out.append(Text("\n"))
            focused = idx == self._index_focus_idx
            style = "bold white" if focused else "#888888"
            desc_style = "#cccccc" if focused else "#444444"
            builder_key = key.split(":", 1)[1]
            checked = builder_key == self._selected_builder
            box = "(*)" if checked else "( )"
            out.append(Text(f"  {box}  ", style=style))
            out.append(Text(label.ljust(name_w), style=style))
            if desc:
                out.append(Text("    "))
                out.append(Text(desc, style=desc_style))
        out.append(Text("\n\n"))
        out.append(
            Text(
                "  up/down  focus    Space  pick    Enter/:go  build    :back  cancel",
                style="#666666",
            )
        )
        with contextlib.suppress(Exception):
            self.query_one("#builder_select_list", Static).update(out)

    def _handle_builder_select_input(self, text: str) -> None:
        self.query_one("#select_input", Input).value = ""
        low = text.lower().strip()

        if low == "" or low in (":go", ":ok"):
            self.run_worker(
                self._start_compile(
                    self._compile_source,
                    index_types=self._selected_index_types,
                    builder=self._selected_builder,
                ),
                exclusive=True,
                group="compile",
            )  # type: ignore[arg-type]
            return

        if not low.startswith(":"):
            self._set_status("  Unknown command (commands start with `:`)")
            return

        if low in (":back", ":cancel"):
            self._enter_index_select(self._compile_source)
            return

        if low in (":q", ":quit", ":exit"):
            self.exit()
            return

        self._set_status(f"  Unknown command: {text}")

    async def _delete_book(self, book: Book) -> None:
        """Delete a book from the store and remove its workspace dir."""
        assert self._repl_context is not None
        self._set_status(f"  deleting: {book.title or '(untitled)'}")
        self._start_spinner("deleting...")
        try:
            await self._repl_context.books_store.delete_book(book.id)
            # Remove workspace directory.
            import shutil

            book_dir = self._repl_context.data_dir / book.id
            if book_dir.exists():
                shutil.rmtree(book_dir, ignore_errors=True)
            self._books = await self._repl_context.list_books()
            await self._refresh_books_list()
            self._set_status(f"  deleted: {book.title or '(untitled)'}.  {len(self._books)} book(s) remaining.")
        except Exception as e:
            self._show_error(f"Failed to delete book:\n{e}")
            self._set_status("  delete failed.")
        finally:
            self._stop_spinner()
            self._focus_input()

    async def _enter_chat(self, book: Book) -> None:
        if not self._repl_context or not self._repl_context.has_chat:
            self._set_status("  chat unavailable: LLM/embedding not configured.")
            return
        self._selected_book = book

        mgr = self._repl_context.session_manager
        sessions = await mgr.list_by_book(book.id)
        if sessions:
            await self._enter_session_select(book, sessions)
        else:
            import random
            import string

            suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            name = f"{(book.title or 'untitled')[:20]}-{suffix}"
            session = await mgr.create(book_id=book.id, name=name, kind="chat")
            self._current_session = session
            self._session_id = session.session_id
            await self._enter_chat_with_session(book, session)

    # -- Session select phase --
    async def _enter_session_select(self, book: Book, sessions: list[Session]) -> None:
        self._session_list = sessions
        self._session_focus_idx = 0
        self._session_select_cross_book = False
        await self._render_session_list()
        self.phase = "session_select"
        self._set_status(f"  {len(sessions)} session(s) for {book.title or '(untitled)'}")
        self._focus_input()

    async def _render_session_list(self) -> None:
        out = Text()
        for idx, sess in enumerate(self._session_list):
            if idx > 0:
                out.append(Text("\n"))
            focused = idx == self._session_focus_idx
            style = "bold white" if focused else "#888888"
            import datetime as _dt

            ts = _dt.datetime.fromtimestamp(sess.updated_at).strftime("%Y-%m-%d %H:%M")
            out.append(Text(f"  {idx + 1:>2}  ", style=style))
            out.append(Text(f"{sess.name[:30]:<30}", style=style))
            out.append(Text(f"  {sess.kind:<10}", style="#666666" if focused else "#444444"))
            out.append(Text(f"  {sess.turn_count:>4} turns", style="#666666" if focused else "#444444"))
            out.append(Text(f"  {ts}", style="#444444"))
        lv = self.query_one("#books_list", ListView)
        await lv.clear()
        lv.append(ListItem(Static(out)))

    async def _render_cross_book_session_list(self) -> None:
        out = Text()
        for idx, sess in enumerate(self._session_list):
            if idx > 0:
                out.append(Text("\n"))
            focused = idx == self._session_focus_idx
            style = "bold white" if focused else "#888888"
            import datetime as _dt

            ts = _dt.datetime.fromtimestamp(sess.updated_at).strftime("%Y-%m-%d %H:%M")
            book_title = "?"
            book = next((b for b in self._books if b.id == sess.book_id), None)
            if book is not None:
                book_title = book.title or "(untitled)"
            out.append(Text(f"  {idx + 1:>2}  ", style=style))
            out.append(Text(f"{sess.name[:25]:<25}", style=style))
            out.append(Text(f"  [{book_title[:20]:<20}]", style="#666666" if focused else "#444444"))
            out.append(Text(f"  {sess.turn_count:>4} turns", style="#666666" if focused else "#444444"))
            out.append(Text(f"  {ts}", style="#444444"))
        lv = self.query_one("#books_list", ListView)
        await lv.clear()
        lv.append(ListItem(Static(out)))

    async def _enter_chat_with_session(self, book: Book, session: Session) -> None:
        self._current_session = session
        self._session_id = session.session_id
        self._chat_markdown = ""

        # Load existing conversation history from the persistent message log.
        if self._repl_context is not None:
            messages = await self._repl_context.session_manager.load_messages(session.session_id)
            if messages:
                md_parts: list[str] = []
                for msg in messages:
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    if role == "user":
                        escaped = content.replace("\n", "\n> ")
                        md_parts.append(f"\n\n> {escaped}\n\n")
                    elif role == "assistant":
                        md_parts.append(f"{content}\n\n")
                self._chat_markdown = "".join(md_parts)

        self.query_one("#chat_log", Markdown).update(self._chat_markdown)
        # Scroll to the end after loading history.
        with contextlib.suppress(Exception):
            chat_log = self.query_one("#chat_log", Markdown)
            chat_log.scroll_end(animate=False)
        self.phase = "chat"
        title = book.title or "(untitled)"
        author = book.author or "Unknown"
        BookScoutTui._set_console_title(f"{title} - {author}")
        hint = f"{title}  by {author}  [{session.name}]"
        with contextlib.suppress(Exception):
            self.query_one("#header_hint", Static).update(hint)
        self._set_status(f"  {session.name}")
        self._focus_input()

    def _find_book_for_session(self, session: Session) -> Book | None:
        return next((b for b in self._books if b.id == session.book_id), None)

    async def _handle_session_select_input(self, text: str) -> None:
        self.query_one("#select_input", Input).value = ""
        low = text.lower().strip()

        if low == "" or low in (":go", ":ok"):
            if not self._session_list:
                self._set_status("  no sessions")
                return
            idx = min(self._session_focus_idx, len(self._session_list) - 1)
            session = self._session_list[idx]
            book = self._find_book_for_session(session)
            if book is None:
                self._set_status(f"  book not found: {session.book_id}")
                return
            self._selected_book = book
            await self._enter_chat_with_session(book, session)
            return

        if not low.startswith(":"):
            self._set_status("  Unknown command (commands start with `:`)")
            return

        if low == ":new":
            if self._selected_book is None and self._session_list:
                self._selected_book = self._find_book_for_session(self._session_list[0])
            if self._selected_book is None:
                self._set_status("  no book selected; use :back to return to books first")
                return
            import random
            import string

            suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
            name = f"{(self._selected_book.title or 'untitled')[:20]}-{suffix}"
            mgr = self._repl_context.session_manager
            session = await mgr.create(book_id=self._selected_book.id, name=name, kind="chat")
            self._session_id = session.session_id
            self._current_session = session
            await self._enter_chat_with_session(self._selected_book, session)
            return

        if low.startswith(":session new"):
            parts = text.split(None, 2)
            name = parts[2].strip() if len(parts) > 2 else None
            if not name:
                import random
                import string

                suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
                name = (
                    f"{(self._selected_book.title or 'untitled')[:20]}-{suffix}"
                    if self._selected_book
                    else f"session-{suffix}"
                )
            book_id = (
                self._selected_book.id
                if self._selected_book
                else (self._session_list[0].book_id if self._session_list else None)
            )
            if book_id is None:
                self._set_status("  no book selected; use :back to return to books first")
                return
            mgr = self._repl_context.session_manager
            session = await mgr.create(book_id=book_id, name=name, kind="chat")
            book = self._find_book_for_session(session)
            if book is None:
                self._set_status(f"  book not found: {book_id}")
                return
            self._selected_book = book
            self._session_id = session.session_id
            self._current_session = session
            await self._enter_chat_with_session(book, session)
            return

        if low == ":resume":
            mgr = self._repl_context.session_manager
            all_sessions = await mgr.list_all()
            if not all_sessions:
                self._set_status("  no sessions to resume")
                return
            self._session_list = all_sessions
            self._session_focus_idx = 0
            self._session_select_cross_book = True
            await self._render_cross_book_session_list()
            self._set_status(f"  {len(all_sessions)} session(s) total")
            return

        if low in (":back", ":cancel", ":select"):
            self.phase = "select"
            await self._refresh_books_list()
            self._set_status(f"  {len(self._books)} book(s)")
            return

        if low in (":q", ":quit", ":exit"):
            self.exit()
            return

        self._set_status(f"  Unknown command: {text}")

    # -- Compile phase --
    async def _start_compile(
        self,
        source_path: str,
        *,
        index_types: set[str] | None = None,
        builder: str = "rule",
    ) -> None:
        assert self._repl_context is not None
        self._clear_error()
        self._post_compile_target = "select"
        self._set_status(f"  compiling: {pathlib.Path(source_path).name}")
        self.phase = "compile"
        self._start_spinner("compiling...")
        try:
            task_id = await self._repl_context.compile(
                source_path,
                index_types=index_types,
                builder=builder,
            )
        except Exception as e:
            self._stop_spinner()
            self._show_error(f"Failed to start compile:\n{e}")
            self._set_status("  failed. type a new path to retry.")
            self.phase = "select"
            self._focus_input()
            return
        self._pending_task_id = task_id
        self._compile_source = source_path
        log = self.query_one("#compile_log", RichLog)
        log.clear()
        log.write(Text(f"source: {source_path}", style="dim"))
        log.write(Text(""))

    def _start_progress_polling(self) -> None:
        self._stop_progress_polling()
        self._progress_timer = self.set_interval(0.5, self._poll_progress)

    def _stop_progress_polling(self) -> None:
        if self._progress_timer is not None:
            self._progress_timer.stop()
            self._progress_timer = None

    async def _poll_progress(self) -> None:
        if self._repl_context is None or self._pending_task_id is None:
            return
        # Render fine-grained monitor snapshots.
        self._render_monitor()
        # Check task status.
        progress = self._repl_context.get_task_progress(self._pending_task_id)
        if progress is None:
            return
        if progress.status in ("succeeded", "failed"):
            self._stop_progress_polling()
            await self._finish_compile(progress)

    def _render_monitor(self) -> None:
        """Render the Monitor's task tree into the compile log."""
        if self._repl_context is None:
            return
        monitor = self._repl_context.monitor
        snapshots = monitor.snapshot()
        if not snapshots:
            return
        log = self.query_one("#compile_log", RichLog)
        log.clear()
        log.write(Text(f"source: {self._compile_source}", style="dim"))
        log.write(Text(""))
        for snap in snapshots:
            indent = "  " * snap.depth
            if snap.total > 0:
                pct = (snap.completed / snap.total * 100) if snap.total > 0 else 0
                filled = int(pct / 100 * 20)
                bar = "\u2588" * filled + "\u2591" * (20 - filled)
                eta_str = f" ETA {int(snap.eta_seconds)}s" if snap.eta_seconds else ""
                status_str = ""
                if snap.status == "done":
                    status_str = " done"
                elif snap.status == "failed":
                    status_str = f" failed: {snap.error or ''}"
                line = Text.assemble(
                    Text(f"{indent}{snap.label}  ", style="bold"),
                    Text(f"{bar} ", style=""),
                    Text(f"{pct:.0f}% ", style="bold"),
                    Text(f"{int(snap.completed)}/{int(snap.total)}", style="dim"),
                    Text(eta_str, style="dim"),
                    Text(status_str, style="green" if snap.status == "done" else "red"),
                )
            else:
                # Indeterminate task 閳?show spinner-like state.
                status_str = ""
                if snap.status == "done":
                    status_str = " done"
                elif snap.status == "failed":
                    status_str = f" failed: {snap.error or ''}"
                elif snap.status == "running":
                    status_str = " ..."
                line = Text.assemble(
                    Text(f"{indent}{snap.label}", style="bold"),
                    Text(status_str, style="green" if snap.status == "done" else "red"),
                )
            log.write(line)

    async def _finish_compile(self, p: TaskProgress) -> None:
        self._stop_spinner()
        self._render_monitor()
        log = self.query_one("#compile_log", RichLog)
        if p.status == "succeeded":
            log.write(Text(""))
            log.write(Text("OK", style="bold green"))
            self._pending_task_id = None
            target = self._post_compile_target
            if target == "chat" and self._selected_book is not None:
                self.phase = "chat"
                self._set_panel("chat")
                self._update_header_hint("chat")
            else:
                self.phase = "select"
                self._set_panel("select")
                self._update_header_hint("select")
            if self._repl_context is not None:
                with contextlib.suppress(Exception):
                    self._books = await self._repl_context.list_books()
            if target == "chat" and self._selected_book is not None:
                self._selected_book = next((b for b in self._books if b.id == self._selected_book.id), None)
                self._set_status(f"  {self._selected_book.title or '(untitled)'}")
            else:
                await self._refresh_books_list()
                self._set_status("  compile OK -- pick a book")
            self._focus_input()
            return
        log.write(Text(""))
        log.write(Text("FAIL", style="bold red"))
        log.write(Text(f"  stage: {p.stage}", style="red"))
        log.write(Text(f"  error: {p.error or '(empty)'}", style="red"))
        self._show_error(
            f"Compile failed.\n"
            f"  stage: {p.stage}\n"
            f"  error: {p.error or '(no error message)'}\n"
            f"  elapsed: {p.elapsed_seconds}s\n"
            f"  task_id: {p.task_id}\n"
            f"  result: {p.result}\n"
            f"  Log: data/logs/repl.log"
        )
        self._set_status("  failed. type a new path to retry.")
        self._pending_task_id = None
        self.phase = "select"
        await self._refresh_books_list()
        self._focus_input()

    # -- Chat phase --
    async def _handle_chat_input(self, text: str) -> None:
        if self.phase == "compile":
            if text.lower() in (":q", ":quit", ":exit"):
                self.exit()
                return
            self.query_one("#chat_input", Input).value = ""
            self._set_status("  please wait... compile in progress")
            return
        if self._chat_busy:
            self._set_status("  please wait...")
            return
        if not text:
            return
        self.query_one("#chat_input", Input).value = ""

        if text.lower() in (":q", ":quit", ":exit"):
            self.exit()
            return
        if text.lower() in (":back", ":select"):
            self._selected_book = None
            await self._refresh_books_list()
            self.phase = "select"
            self._focus_input()
            return
        if text.lower() == ":clear":
            self._chat_markdown = ""
            self.query_one("#chat_log", Markdown).update("")
            return

        if text.lower() in (":bottom", ":end"):
            self.query_one("#chat_log", Markdown).scroll_end(animate=False)
            return

        low = text.lower()
        if low.startswith(":addindex ") or low.startswith(":addidx "):
            parts = text.split()
            if len(parts) < 2:
                self._set_status("  usage: :addindex <type>")
                return
            idx_type = parts[1].lower()
            assert self._repl_context is not None
            assert self._selected_book is not None
            # Validate known provider.
            provider = self._repl_context.registry.by_type(idx_type)
            if provider is None:
                self._set_status(f"  unknown index: {idx_type}")
                return
            built = set(self._selected_book.indexes)
            if idx_type in built:
                self._set_status(f"  {idx_type} already built")
                return
            self.run_worker(
                self._start_add_index(self._selected_book.id, {idx_type}),
                exclusive=True,
                group="compile",
            )  # type: ignore[arg-type]
            return

        if low.startswith(":rmindex ") or low.startswith(":rmidx "):
            parts = text.split()
            if len(parts) < 2:
                self._set_status("  usage: :rmindex <type>")
                return
            idx_type = parts[1].lower()
            assert self._repl_context is not None
            assert self._selected_book is not None
            built = set(self._selected_book.indexes)
            if idx_type not in built:
                self._set_status(f"  {idx_type} not built for this book")
                return
            self.run_worker(
                self._do_rm_index(self._selected_book.id, idx_type),
                exclusive=True,
                group="compile",
            )  # type: ignore[arg-type]
            return

        if low == ":compact":
            self._set_status("  compacting conversation...")
            self.run_worker(
                self._run_compact(),
                exclusive=True,
                group="chat",
            )  # type: ignore[arg-type]
            return

        if low == ":resume":
            mgr = self._repl_context.session_manager
            all_sessions = await mgr.list_all()
            if not all_sessions:
                self._set_status("  no sessions to resume")
                return
            self._session_list = all_sessions
            self._session_focus_idx = 0
            self._session_select_cross_book = True
            await self._render_cross_book_session_list()
            self.phase = "session_select"
            self._set_status(f"  {len(all_sessions)} session(s) total")
            return

        if low.startswith(":rename "):
            parts = text.split(None, 1)
            if len(parts) < 2:
                self._set_status("  usage: :rename <NAME>")
                return
            new_name = parts[1].strip()
            if self._current_session:
                await self._repl_context.session_manager.rename(self._current_session.session_id, new_name)
                self._current_session = await self._repl_context.session_manager.get(self._current_session.session_id)
                self._set_status(f"  renamed to: {new_name}")
            return

        if low.startswith(":session new "):
            parts = text.split(None, 2)
            name = parts[2].strip() if len(parts) > 2 else None
            if not name:
                import random
                import string

                suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
                name = f"{(self._selected_book.title or 'untitled')[:20]}-{suffix}"
            if self._selected_book is None:
                self._set_status("  no book selected")
                return
            mgr = self._repl_context.session_manager
            session = await mgr.create(book_id=self._selected_book.id, name=name, kind="chat")
            self._session_id = session.session_id
            self._current_session = session
            await self._enter_chat_with_session(self._selected_book, session)
            return

        if low == ":session list":
            assert self._selected_book is not None
            sessions = await self._repl_context.session_manager.list_by_book(self._selected_book.id)
            lines = ["**Sessions for this book:**"]
            for s in sessions:
                lines.append(f"- {s.name} ({s.kind}, {s.turn_count} turns)")
            self._chat_markdown += "\n" + "\n".join(lines) + "\n\n"
            log = self.query_one("#chat_log", Markdown)
            await log.update(self._chat_markdown)
            log.scroll_end(animate=False)
            return

        if low.startswith(":"):
            self._set_status(f"  Unknown chat command: {text}")
            return

        self.run_worker(self._run_chat(text), exclusive=True, group="chat")  # type: ignore[arg-type]

    async def _run_compact(self) -> None:
        """Force-compact the conversation history via :compact command."""
        assert self._repl_context is not None
        assert self._session_id is not None
        mode = self._repl_context._modes.get(self._session_id)
        if mode is None:
            self._set_status("  no active reading mode to compact")
            return
        try:
            summary = await mode.compact()
            if summary:
                self._chat_markdown += f"\n*[compacted] — {summary}*\n\n"
            else:
                self._chat_markdown += "\n*[compacted] — (too few messages to compact)*\n\n"
            log = self.query_one("#chat_log", Markdown)
            await log.update(self._chat_markdown)
            log.scroll_end(animate=False)
            self._set_status("  compaction done")
        except Exception as e:
            self._set_status(f"  compaction failed: {e}")
        finally:
            self._focus_input()

    async def _run_chat(self, user_input: str) -> None:
        assert self._repl_context is not None
        assert self._selected_book is not None
        assert self._session_id is not None
        # Append the user turn as a markdown blockquote and flush to the widget.
        escaped = user_input.replace("\n", "\n> ")
        self._chat_markdown += f"\n\n> {escaped}\n\n"
        log = self.query_one("#chat_log", Markdown)
        await log.update(self._chat_markdown)
        log.scroll_end(animate=False)
        self._chat_busy = True
        self._set_status("  thinking...")
        self._start_spinner("thinking...")
        self._streaming_buffer = []
        self._streaming_started = False
        response_text_parts: list[str] = []
        try:
            async for chunk in self._repl_context.chat(
                self._selected_book.id,
                self._session_id,
                user_input,
            ):
                if chunk.kind == "text":
                    delta = chunk.data if isinstance(chunk.data, str) else str(chunk.data)
                    response_text_parts.append(delta)
                self._handle_chunk(chunk)
        except Exception as e:
            self._chat_markdown += f"\n**ERROR:** {e}\n\n"
            await self.query_one("#chat_log", Markdown).update(self._chat_markdown)
        finally:
            self._flush_streaming()
            self._chat_busy = False
            self._stop_spinner()
            # Update session record + persist messages.
            if self._current_session and self._repl_context:
                response_text = "".join(response_text_parts)
                mgr = self._repl_context.session_manager
                await mgr.update_after_turn(
                    self._current_session.session_id,
                    user_input=user_input,
                    response_text=response_text,
                )
                await mgr.append_message(
                    self._current_session.session_id,
                    role="user",
                    content=user_input,
                )
                await mgr.append_message(
                    self._current_session.session_id,
                    role="assistant",
                    content=response_text,
                )
            self._set_status(f"  {self._selected_book.title or '(untitled)'}")
            self._focus_input()

    async def _start_add_index(
        self,
        book_id: str,
        index_types: set[str],
        *,
        post_target: str = "chat",
    ) -> None:
        assert self._repl_context is not None
        self._post_compile_target = post_target
        self._set_status(f"  building: {','.join(sorted(index_types))}")
        self.phase = "compile"
        self._start_spinner("building index...")
        try:
            task_id = await self._repl_context.add_index(book_id, index_types)
        except Exception as e:
            self._stop_spinner()
            self._show_error(f"Failed to start index build:\n{e}")
            self.phase = post_target
            self._focus_input()
            return
        self._pending_task_id = task_id
        log = self.query_one("#compile_log", RichLog)
        log.clear()
        log.write(Text(f"building indexes: {','.join(sorted(index_types))}", style="dim"))
        log.write(Text(""))

    async def _do_rm_index(self, book_id: str, idx_type: str) -> None:
        assert self._repl_context is not None
        self._set_status(f"  removing: {idx_type}")
        try:
            await self._repl_context.remove_index(book_id, idx_type)
            self._books = await self._repl_context.list_books()
            self._selected_book = next((b for b in self._books if b.id == book_id), None)
            if self.phase == "chat":
                self._chat_markdown += f"\n*removed index: `{idx_type}`*\n\n"
                log = self.query_one("#chat_log", Markdown)
                await log.update(self._chat_markdown)
                log.scroll_end(animate=False)
            else:
                await self._refresh_books_list()
            self._set_status(
                f"  removed {idx_type} from #{self._books.index(self._selected_book) + 1 if self._selected_book else '-'}"
            )
        except Exception as e:
            self._show_error(f"Failed to remove index:\n{e}")
            self._set_status("  rmindex failed.")
        finally:
            self._focus_input()

    def _handle_chunk(self, chunk: StreamChunk) -> None:
        log = self.query_one("#chat_log", Markdown)
        if chunk.kind == "text":
            delta = chunk.data if isinstance(chunk.data, str) else str(chunk.data)
            if not self._streaming_started:
                self._streaming_started = True
            self._streaming_buffer.append(delta)
            joined = "".join(self._streaming_buffer)
            if "\n" in joined:
                head, _, tail = joined.rpartition("\n")
                self._streaming_buffer = [tail]
                self._write_assistant_line(head)
        elif chunk.kind == "tool_call":
            self._flush_streaming()
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            self._chat_markdown += f"\n`-> {name}`\n\n"
            log.update(self._chat_markdown)
            log.scroll_end(animate=False)
        elif chunk.kind == "tool_result":
            self._flush_streaming()
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            summary = data.get("summary", "")
            stats = data.get("retrieval_stats") or {}
            stats_str = ", ".join(f"{k}={v}" for k, v in stats.items())
            buf = f"`<- {name}`"
            if summary:
                buf += f"  _{summary}_"
            if stats_str:
                buf += f"  `[{stats_str}]`"
            self._chat_markdown += f"\n{buf}\n\n"
            if self._verbose_tools:
                self._chat_markdown += self._render_verbose_tool(data)
            log.update(self._chat_markdown)
            log.scroll_end(animate=False)
        elif chunk.kind == "status":
            data = chunk.data if isinstance(chunk.data, dict) else {}
            phase = data.get("phase", "")
            if phase == "auto_compacted":
                self._chat_markdown += "\n*[auto-compacted]*\n\n"
                log.update(self._chat_markdown)
                log.scroll_end(animate=False)

    def _flush_streaming(self) -> None:
        if not self._streaming_started:
            return
        text = "".join(self._streaming_buffer)
        self._streaming_buffer = []
        self._streaming_started = False
        if text:
            self._write_assistant_line(text)
            self._chat_markdown += "\n"
            log = self.query_one("#chat_log", Markdown)
            log.update(self._chat_markdown)
            log.scroll_end(animate=False)

    def _write_assistant_line(self, text: str) -> None:
        self._chat_markdown += f"{text}\n"

    @staticmethod
    def _render_verbose_tool(data: dict) -> str:
        """Render full params and result for a tool call (verbose mode)."""

        out = ""
        args = data.get("arguments") or {}
        if args:
            try:
                args_json = json.dumps(args, indent=2, ensure_ascii=False)
            except (TypeError, ValueError):
                args_json = str(args)
            out += f"  _params:_\n\n```json\n{args_json}\n```\n\n"
        result_text = data.get("result_text", "")
        if result_text:
            try:
                parsed = json.loads(result_text)
                result_json = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                result_json = result_text
            out += f"  _result:_\n\n```json\n{result_json}\n```\n\n"
        return out

    # -- Actions --
    def action_clear_log(self) -> None:
        if self.phase == "chat":
            self._chat_markdown = ""
            log = self.query_one("#chat_log", Markdown)
            log.update("")
            log.scroll_end(animate=False)
        elif self.phase == "compile":
            self.query_one("#compile_log", RichLog).clear()

    def action_toggle_verbose_tools(self) -> None:
        self._verbose_tools = not self._verbose_tools
        self._set_status(f"  verbose tool calls: {'ON' if self._verbose_tools else 'OFF'}")

    def action_handle_ctrl_c(self) -> None:
        if self._quit_pending:
            self.exit()
            return
        self._quit_pending = True
        self._set_status("  Press Ctrl+C again to quit")
        self.set_timer(3, self._reset_quit_pending)

    def _reset_quit_pending(self) -> None:
        self._quit_pending = False

    async def action_quit(self) -> None:
        self.exit()

    # -- Command palette --
    _COMMANDS: list[tuple[str, str, tuple[str, ...]]] = [
        ("back", "Return to the book list", ("chat", "session_select", "index_select", "builder_select")),
        ("bottom", "Scroll to the bottom of the chat", ("chat",)),
        ("clear", "Clear the chat log", ("chat",)),
        ("compact", "Manually compact conversation history", ("chat",)),
        ("quit", "Exit BookScout", ("select", "chat", "compile", "session_select", "index_select", "builder_select")),
        ("compile", "Compile a new book from a source file", ("select",)),
        ("delete", "Delete book N: :delete N", ("select",)),
        ("addindex", "Build an index for book N: :addindex N <type>", ("select", "chat")),
        ("rmindex", "Remove an index from book N: :rmindex N <type>", ("select", "chat")),
        ("go", "Confirm and proceed", ("index_select", "builder_select")),
        ("cancel", "Cancel and go back", ("index_select", "builder_select")),
        ("resume", "Resume a previous session", ("select", "chat", "session_select")),
        ("rename", "Rename current session: :rename <NAME>", ("chat",)),
        ("session new", "Create a new session: :session new <name>", ("chat", "session_select")),
        ("session list", "List sessions for current book", ("chat",)),
        ("new", "Create a new session for the current book", ("session_select",)),
    ]

    def _palette_commands(self) -> list[tuple[str, str]]:
        return [(cmd, desc) for cmd, desc, phases in self._COMMANDS if self.phase in phases]

    def _active_input(self) -> Input:
        return self.query_one(
            "#chat_input" if self.phase in ("chat", "compile") else "#select_input",
            Input,
        )

    def _open_palette(self) -> None:
        if self._palette_open:
            self._render_palette()
            return
        self._palette_open = True
        self._palette_focus_idx = 0
        self._render_palette()

    def _close_palette(self) -> None:
        self._palette_open = False
        self._palette_focus_idx = 0
        self._skip_palette = False
        self._quit_pending = False
        with contextlib.suppress(Exception):
            self.query_one("#command_palette", Container).display = False

    def _palette_move(self, delta: int) -> None:
        self._palette_focus_idx = max(0, self._palette_focus_idx + delta)
        self._render_palette()

    def _accept_palette(self) -> None:
        try:
            inp = self._active_input()
            query = inp.value.lstrip(":").lower()
            all_cmds = self._palette_commands()
            filtered = [(cmd, desc) for cmd, desc in all_cmds if not query or query in cmd.lower()]
            if not filtered:
                self._close_palette()
                return
            self._palette_focus_idx = max(0, min(self._palette_focus_idx, len(filtered) - 1))
            cmd = filtered[self._palette_focus_idx][0]
        except Exception:
            self._close_palette()
            return

        self._close_palette()
        inp = self._active_input()
        inp.value = f":{cmd} "
        inp.action_end()
        inp.focus()
        self._skip_palette = False

    def _render_palette(self) -> None:
        try:
            inp = self._active_input()
            query = inp.value.lstrip(":").lower()
        except Exception:
            return
        all_cmds = self._palette_commands()
        filtered = [(cmd, desc) for cmd, desc in all_cmds if not query or query in cmd.lower()]
        if not filtered:
            self._close_palette()
            return
        # Clamp focus.
        self._palette_focus_idx = max(0, min(self._palette_focus_idx, len(filtered) - 1))
        # Render as Static text — avoid ListView focus / highlight issues.
        out = Text()
        for i, (cmd, desc) in enumerate(filtered):
            if i > 0:
                out.append(Text("\n"))
            focused = i == self._palette_focus_idx
            style = "bold white" if focused else "#888888"
            out.append(Text(f"  :{cmd}  ", style=style))
            out.append(Text(desc, style="#666666" if focused else "#444444"))
        palette = self.query_one("#command_palette", Container)
        with contextlib.suppress(Exception):
            self.query_one("#palette_list", Static).update(out)
        palette.display = True
        palette.styles.height = min(len(filtered) + 1, 14)


__all__ = ["BookScoutTui"]
