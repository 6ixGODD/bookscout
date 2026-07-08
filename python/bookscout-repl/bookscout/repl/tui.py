"""BookScout TUI — vim-like minimal terminal UI over ReplContext.

Layout::

    Header (phase-specific hint)
    ─────────────────────  (white bold line)
    Content area           (book list / chat log / compile log / index select)
    ─────────────────────  (white bold line)
    > command              (the `>` is baked into the Input value, non-deletable)
    ─────────────────────  (white bold line)
    Status

Pure black background. No borders. Focus always on input.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import typing as t

from rich.text import Text
from textual import on
from textual.app import App
from textual.app import ComposeResult
from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Checkbox
from textual.widgets import Input
from textual.widgets import ListItem
from textual.widgets import ListView
from textual.widgets import RichLog
from textual.widgets import Rule
from textual.widgets import Static

from .config import BookScoutConfig
from .context import ReplContext

if t.TYPE_CHECKING:
    from bookscout.agents.mode import StreamChunk
    from bookscout.books import Book
    from bookscout.doccompiler.task_manager import TaskProgress


class PromptInput(Input):
    """An :class:`Input` whose value always begins with a non-deletable
    ``"> "`` prompt prefix.

    The prefix is baked into ``value`` itself (not a separate widget), so the
    cursor sits next to it on the same line and the user cannot backspace
    past it. The stripped command text is exposed via :attr:`command`.
    """

    PROMPT = "> "

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        kwargs.setdefault("value", self.PROMPT)
        super().__init__(*args, **kwargs)

    @property
    def command(self) -> str:
        """The current value with the prompt prefix stripped."""
        v = self.value
        return v[len(self.PROMPT):] if v.startswith(self.PROMPT) else v

    def reset(self) -> None:
        """Reset back to just the prompt prefix and place the cursor after it."""
        self.value = self.PROMPT
        self.cursor_position = len(self.PROMPT)

    # -- Block any deletion that overlaps the prompt prefix --
    def delete(self, start: int, end: int) -> None:
        p = len(self.PROMPT)
        # Entire deletion is inside the prefix zone — block.
        if end <= p:
            return
        # Partial overlap with the prefix — clamp start to the prefix end.
        if start < p:
            start = p
        super().delete(start, end)

    # -- Clamp cursor / selection so position never drops below the prefix --
    def action_cursor_left(self, select: bool = False) -> None:
        p = len(self.PROMPT)
        if not select and self.cursor_position <= p:
            return
        super().action_cursor_left(select)
        if self.cursor_position < p:
            self.cursor_position = p

    def action_cursor_left_word(self, select: bool = False) -> None:
        p = len(self.PROMPT)
        if not select and self.cursor_position <= p:
            return
        super().action_cursor_left_word(select)
        if self.cursor_position < p:
            self.cursor_position = p

    def action_home(self, select: bool = False) -> None:
        super().action_home(select)
        p = len(self.PROMPT)
        if self.cursor_position < p:
            self.cursor_position = p

    # -- Submit sends the stripped command (without prompt) to handlers --
    async def action_submit(self) -> None:
        validation_result = (
            self.validate(self.value) if "submitted" in self.validate_on else None
        )
        self.post_message(self.Submitted(self, self.command, validation_result))


class BookScoutTui(App[None]):
    """The BookScout terminal UI."""

    CSS = """
    Screen {
        background: #000000;
        color: #c0c0c0;
    }
    #status_bar {
        dock: bottom;
        height: 1;
        color: #888888;
        padding: 0 1;
    }
    #header {
        layout: vertical;
        height: auto;
        padding: 0 1;
    }
    #header_brand {
        color: #ffffff;
        text-style: bold;
        height: 1;
    }
    #header_hint {
        color: #666666;
        height: auto;
    }
    #header_rule {
        color: #ffffff;
        height: 1;
    }
    #main {
        layout: vertical;
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    .log-area {
        height: 1fr;
    }
    #index_select_hint {
        color: #c0c0c0;
        height: 1;
    }
    #spinner_line, #chat_spinner_line {
        height: 1;
        color: #666666;
        padding: 0 0 0 0;
    }
    #input_area {
        height: 3;
        padding: 0 0 0 0;
    }
    #select_input, #chat_input {
        width: 1fr;
    }
    #error_display {
        color: #cc6666;
        padding: 0 1;
        height: auto;
        max-height: 6;
    }
    Rule {
        color: #ffffff;
        background: #000000;
    }
    Input {
        border-top: solid #ffffff;
        border-bottom: solid #ffffff;
        border-left: none;
        border-right: none;
        background: #000000;
        color: #c0c0c0;
        padding: 0 0 0 0;
        height: 3;
    }
    Input:focus {
        border-top: solid #ffffff;
        border-bottom: solid #ffffff;
    }
    Checkbox {
        padding: 0 1;
        background: #000000;
    }
    ListView > ListItem {
        padding: 0 1;
        background: #000000;
    }
    ListView:focus > ListItem.--highlight {
        background: #333333;
    }
    ProgressBar {
        background: #333333;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
    ]

    phase: reactive[str] = reactive("init", layout=True)

    def __init__(
        self,
        config: BookScoutConfig,
        *,
        initial_book_id: str | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._initial_book_id = initial_book_id
        self._repl_context: ReplContext | None = None
        self._books: list[Book] = []
        self._selected_book: Book | None = None
        self._pending_task_id: str | None = None
        self._progress_timer: t.Any = None
        self._streaming_buffer: list[str] = []
        self._streaming_started = False
        self._chat_busy = False
        self._assistant_first_line = True
        self._spinner_frames = ["|", "/", "-", "\\"]
        self._spinner_idx = 0
        self._spinner_timer: t.Any = None
        self._spinner_active = False
        self._compile_source = ""
        self._selected_index_types: set[str] = set()
        self._post_compile_target = "select"

    # -- Composition --
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
                yield Checkbox("", id="cb_chunk")
                yield Checkbox("", id="cb_summary")
                yield Checkbox("", id="cb_graph")
                yield Static("", id="index_select_error")
            # Compile panel
            with Container(id="compile_panel"):
                yield RichLog(id="compile_log", markup=True, wrap=True, classes="log-area")
                yield Static("", id="spinner_line")
            # Chat panel
            with Container(id="chat_panel"):
                yield RichLog(id="chat_log", markup=True, wrap=True, classes="log-area")
                yield Static("", id="chat_spinner_line")
        with Container(id="input_area"):
            yield PromptInput(id="select_input")
            yield PromptInput(id="chat_input")
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

        if self._initial_book_id:
            book = next((b for b in self._books if b.id == self._initial_book_id), None)
            if book is not None:
                self._selected_book = book
                self.phase = "chat"
                self._set_status(f"  {book.title or '(untitled)'}")
                self._focus_input()
                return

        self._refresh_books_list()
        self.phase = "select"
        self._set_status(
            f"  {len(self._books)} book(s)"
            + ("" if ctx.has_chat else "  [no LLM/embedding]")
        )
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
            if self.phase in ("select", "index_select"):
                self.query_one("#select_input", PromptInput).focus()
            elif self.phase == "chat":
                self.query_one("#chat_input", PromptInput).focus()

    def _header_hint_for_phase(self, phase: str) -> str:
        if phase == "select":
            return (
                ":book N  read    "
                ":compile <path>  add    "
                ":delete N  remove    "
                ":quit  quit"
            )
        if phase == "index_select":
            return "Space/Enter  toggle    :go  build    :back  cancel"
        if phase == "compile":
            return "compiling..."
        if phase == "chat":
            return (
                ":back  books    "
                ":clear  clear    "
                ":addindex X / :rmindex X  indexes    "
                ":quit  quit"
            )
        return ""

    def _update_header_hint(self, phase: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#header_hint", Static).update(self._header_hint_for_phase(phase))

    def _set_panel(self, phase: str) -> None:
        panel_map = {
            "select": "select_panel",
            "index_select": "index_select_panel",
            "compile": "compile_panel",
            "chat": "chat_panel",
        }
        active = panel_map.get(phase, "")
        for panel_id in ("select_panel", "index_select_panel", "compile_panel", "chat_panel"):
            with contextlib.suppress(Exception):
                self.query_one(f"#{panel_id}", Container).display = (panel_id == active)
        # Show the right input.
        with contextlib.suppress(Exception):
            self.query_one("#select_input", PromptInput).display = (phase in ("select", "index_select"))
            self.query_one("#chat_input", PromptInput).display = (phase in ("chat", "compile"))

    def _set_status(self, text: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#status_bar", Static).update(text)

    def _show_error(self, message: str) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#error_display", Static).update(message)

    def _clear_error(self) -> None:
        with contextlib.suppress(Exception):
            self.query_one("#error_display", Static).update("")

    def _refresh_books_list(self) -> None:
        lv = self.query_one("#books_list", ListView)
        lv.clear()
        registry = self._repl_context.registry if self._repl_context else None
        for idx, book in enumerate(self._books, start=1):
            title = book.title or "(untitled)"
            author = book.author or "Unknown"
            flags: list[Text] = []
            if registry is not None:
                built = set(book.indexes)
                for provider in registry.all():
                    mark = "√" if provider.index_type in built else "×"
                    style = "bold white" if provider.index_type in built else "dim"
                    flags.append(Text(f" {mark} {provider.display_name} ", style=style))
            else:
                built_count = len(book.indexes) if book.indexes else 0
                flags.append(Text(f" {built_count} idx", style="dim"))

            label = Text.assemble(
                Text(f"{idx:>2}  ", style="bold"),
                Text(title, style="bold"),
                Text(f"  {author}", style="dim"),
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
    def _book_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "books_list":
            return
        idx = event.list_view.index
        if idx is None or idx >= len(self._books):
            return
        self._enter_chat(self._books[idx])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "select_input":
            if self.phase == "index_select":
                self._handle_index_select_input(event.value.strip())
            else:
                self._handle_select_input(event.value.strip())
        elif event.input.id == "chat_input":
            self._handle_chat_input(event.value.strip())

    @staticmethod
    def _clean_path(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        return value.strip()

    def _handle_select_input(self, value: str) -> None:
        if not value:
            return
        self.query_one("#select_input", PromptInput).reset()
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
        # :book N — enter chat for a single book.
        if low.startswith(":book") or low.startswith(":b "):
            parts = value.split(None, 1)
            if len(parts) < 2 or not parts[1].strip():
                self._set_status("  usage: :book N")
                return
            arg = parts[1].strip()
            if "," in arg:
                self._set_status("  multi-select not supported yet")
                return
            if not arg.isdigit():
                self._set_status("  usage: :book N")
                return
            idx = int(arg) - 1
            if 0 <= idx < len(self._books):
                self._enter_chat(self._books[idx])
            else:
                self._set_status(f"  no book #{arg}")
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
        # :delete N — remove a book and its workspace.
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
        self._render_index_select()
        self.phase = "index_select"
        self._set_status(f"  select indexes for: {pathlib.Path(source_path).name}")
        self._focus_input()

    def _render_index_select(self) -> None:
        """Render the checkbox list for index selection.

        Each Checkbox gets a Rich-Text label: bold display_name + dim description.
        The Checkbox's ``.value`` is set from ``self._selected_index_types``.
        """
        assert self._repl_context is not None
        registry = self._repl_context.registry
        for provider in registry.all():
            cb_id = f"#cb_{provider.index_type}"
            try:
                cb = self.query_one(cb_id, Checkbox)
            except Exception:
                continue
            parts: list[Text] = [Text(provider.display_name, style="bold")]
            if provider.description:
                parts.append(Text("  "))
                parts.append(Text(provider.description, style="dim"))
            cb.label = Text.assemble(*parts)
            cb.value = provider.index_type in self._selected_index_types

    @on(Checkbox.Changed)
    def _checkbox_changed(self, event: Checkbox.Changed) -> None:
        """Keep ``self._selected_index_types`` in sync with the checkbox widgets."""
        cb_id = event.checkbox.id or ""
        if not cb_id.startswith("cb_"):
            return
        index_type = cb_id[3:]
        if event.value:
            self._selected_index_types.add(index_type)
        else:
            self._selected_index_types.discard(index_type)
        names = ", ".join(sorted(self._selected_index_types)) or "none"
        self._set_status(f"  indexes: {names}")

    def _handle_index_select_input(self, text: str) -> None:
        """Handle input in the index_select phase.

        Toggle is done via the Checkbox widgets themselves; the input box only
        accepts `:go`/Enter (confirm), `:back` (cancel), `:quit` (exit).
        """
        self.query_one("#select_input", PromptInput).reset()
        low = text.lower().strip()

        if low == "":
            # Empty Enter = confirm.
            if self._selected_index_types:
                self.run_worker(
                    self._start_compile(self._compile_source, index_types=self._selected_index_types),
                    exclusive=True, group="compile",
                )  # type: ignore[arg-type]
            else:
                self._set_status("  select at least one index")
            return

        if not low.startswith(":"):
            self._set_status("  Unknown command (commands start with `:`)")
            return

        if low in (":go", ":ok"):
            if self._selected_index_types:
                self.run_worker(
                    self._start_compile(self._compile_source, index_types=self._selected_index_types),
                    exclusive=True, group="compile",
                )  # type: ignore[arg-type]
            else:
                self._set_status("  select at least one index")
            return

        if low in (":back", ":cancel", ":select"):
            self.phase = "select"
            self._set_status(f"  {len(self._books)} book(s)")
            self._focus_input()
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
            self._refresh_books_list()
            self._set_status(f"  deleted: {book.title or '(untitled)'}.  {len(self._books)} book(s) remaining.")
        except Exception as e:
            self._show_error(f"Failed to delete book:\n{e}")
            self._set_status("  delete failed.")
        finally:
            self._stop_spinner()
            self._focus_input()

    def _enter_chat(self, book: Book) -> None:
        if not self._repl_context or not self._repl_context.has_chat:
            self._set_status("  chat unavailable: LLM/embedding not configured.")
            return
        self._selected_book = book
        log = self.query_one("#chat_log", RichLog)
        log.clear()
        log.write(Text.assemble(
            Text(book.title or "(untitled)", style="bold"),
            Text(f"  by {book.author or 'Unknown'}", style="dim"),
        ))
        log.write(Text(""))
        log.write(Text("  :quit :back :clear", style="dim"))
        log.write(Text(""))
        self.phase = "chat"
        self._set_status(f"  {book.title or '(untitled)'}")
        self._focus_input()

    # -- Compile phase --
    async def _start_compile(self, source_path: str, *, index_types: set[str] | None = None) -> None:
        assert self._repl_context is not None
        self._clear_error()
        self._post_compile_target = "select"
        self._set_status(f"  compiling: {pathlib.Path(source_path).name}")
        self.phase = "compile"
        self._start_spinner("compiling...")
        try:
            task_id = await self._repl_context.compile(source_path, index_types=index_types)
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
                bar = "█" * filled + "░" * (20 - filled)
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
                # Indeterminate task — show spinner-like state.
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
            if self._repl_context is not None:
                self._books = await self._repl_context.list_books()
            self._pending_task_id = None
            target = self._post_compile_target
            if target == "chat" and self._selected_book is not None:
                self._selected_book = next(
                    (b for b in self._books if b.id == self._selected_book.id), None
                )
                self.phase = "chat"
                self._set_status(f"  {self._selected_book.title or '(untitled)'}")
            else:
                self.phase = "select"
                self._refresh_books_list()
                self._set_status("  compile OK — pick a book")
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
        self._refresh_books_list()
        self._focus_input()

    # -- Chat phase --
    def _handle_chat_input(self, text: str) -> None:
        if self.phase == "compile":
            if text.lower() in (":q", ":quit", ":exit"):
                self.exit()
                return
            self.query_one("#chat_input", PromptInput).reset()
            self._set_status("  please wait... compile in progress")
            return
        if self._chat_busy:
            self._set_status("  please wait...")
            return
        if not text:
            return
        self.query_one("#chat_input", PromptInput).reset()

        if text.lower() in (":q", ":quit", ":exit"):
            self.exit()
            return
        if text.lower() in (":back", ":select"):
            self._selected_book = None
            self._refresh_books_list()
            self.phase = "select"
            self._focus_input()
            return
        if text.lower() == ":clear":
            self.query_one("#chat_log", RichLog).clear()
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
                exclusive=True, group="compile",
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
                exclusive=True, group="compile",
            )  # type: ignore[arg-type]
            return

        if low.startswith(":"):
            self._set_status(f"  Unknown chat command: {text}")
            return

        self.run_worker(self._run_chat(text), exclusive=True, group="chat")  # type: ignore[arg-type]

    async def _run_chat(self, user_input: str) -> None:
        assert self._repl_context is not None
        assert self._selected_book is not None
        log = self.query_one("#chat_log", RichLog)
        log.write(Text.assemble(
            Text("> ", style="bold"),
            Text(user_input),
        ))
        log.write(Text(""))
        self._chat_busy = True
        self._set_status("  thinking...")
        self._start_spinner("thinking...")
        self._streaming_buffer = []
        self._streaming_started = False
        self._assistant_first_line = True
        try:
            async for chunk in self._repl_context.chat(self._selected_book.id, user_input):
                self._handle_chunk(chunk, log)
        except Exception as e:
            log.write(Text(f"ERROR: {e}", style="bold red"))
        finally:
            self._flush_streaming(log)
            self._chat_busy = False
            self._stop_spinner()
            self._set_status(f"  {self._selected_book.title or '(untitled)'}")
            self._focus_input()

    async def _start_add_index(self, book_id: str, index_types: set[str]) -> None:
        assert self._repl_context is not None
        self._post_compile_target = "chat"
        self._set_status(f"  building: {','.join(sorted(index_types))}")
        self.phase = "compile"
        self._start_spinner("building index...")
        try:
            task_id = await self._repl_context.add_index(book_id, index_types)
        except Exception as e:
            self._stop_spinner()
            self._show_error(f"Failed to start index build:\n{e}")
            self.phase = "chat"
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
            # Refresh selected_book indexes.
            self._books = await self._repl_context.list_books()
            self._selected_book = next((b for b in self._books if b.id == book_id), None)
            chat_log = self.query_one("#chat_log", RichLog)
            chat_log.write(Text(f"  removed index: {idx_type}", style="dim"))
            self._set_status(f"  {self._selected_book.title or '(untitled)'}")
        except Exception as e:
            self._show_error(f"Failed to remove index:\n{e}")
            self._set_status("  rmindex failed.")
        finally:
            self._focus_input()

    def _handle_chunk(self, chunk: StreamChunk, log: RichLog) -> None:
        if chunk.kind == "text":
            delta = chunk.data if isinstance(chunk.data, str) else str(chunk.data)
            if not self._streaming_started:
                self._streaming_started = True
            self._streaming_buffer.append(delta)
            joined = "".join(self._streaming_buffer)
            if "\n" in joined:
                head, _, tail = joined.rpartition("\n")
                self._streaming_buffer = [tail]
                self._write_assistant_line(head, log=log)
        elif chunk.kind == "tool_call":
            self._flush_streaming(log)
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            log.write(Text.assemble(
                Text("  -> ", style="bold"),
                Text(name),
            ))
            self._assistant_first_line = True
        elif chunk.kind == "tool_result":
            self._flush_streaming(log)
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            summary = data.get("summary", "")
            stats = data.get("retrieval_stats") or {}
            stats_str = ", ".join(f"{k}={v}" for k, v in stats.items())
            parts = [
                Text("  <- ", style="bold"),
                Text(name),
            ]
            if summary:
                parts.append(Text(f"  {summary}", style="dim"))
            if stats_str:
                parts.append(Text(f"  [{stats_str}]", style="dim"))
            log.write(Text.assemble(*parts))
            self._assistant_first_line = True
        elif chunk.kind == "status":
            data = chunk.data if isinstance(chunk.data, dict) else {}
            phase = data.get("phase", "")
            if phase == "auto_compacted":
                log.write(Text("  [auto-compacted]", style="dim"))

    def _flush_streaming(self, log: RichLog) -> None:
        if not self._streaming_started:
            return
        text = "".join(self._streaming_buffer)
        self._streaming_buffer = []
        self._streaming_started = False
        if text:
            self._write_assistant_line(text, log=log)
            log.write(Text(""))

    def _write_assistant_line(self, text: str, *, log: RichLog) -> None:
        if self._assistant_first_line:
            log.write(Text.assemble(
                Text("< ", style="bold"),
                Text(text),
            ))
            self._assistant_first_line = False
        else:
            log.write(Text(f"  {text}"))

    # -- Actions --
    def action_clear_log(self) -> None:
        if self.phase == "chat":
            self.query_one("#chat_log", RichLog).clear()
        elif self.phase == "compile":
            self.query_one("#compile_log", RichLog).clear()

    async def action_quit(self) -> None:
        self.exit()


__all__ = ["BookScoutTui"]
