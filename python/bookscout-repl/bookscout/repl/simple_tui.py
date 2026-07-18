"""Full-screen TUI using prompt_toolkit Layout + rich rendering.

Replaces the Textual-based TUI with a vim-like terminal interface:
- prompt_toolkit Application with full-screen Layout (output + status + input)
- rich renders markdown/tables to ANSI to prompt_toolkit FormattedTextControl
- Key bindings: Up/Down/j/k for navigation, Enter/Space for selection
- Streaming chat with incremental rendering via app.invalidate()
- Tool calls rendered in dim gray so visual focus stays on model text

Phases: select, session_select, chat (primary).
Also: index_select, builder_select, compile (transient).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import pathlib
import random
import string
import typing as t

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.formatted_text import to_formatted_text
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import BufferControl
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text

from .config import BookScoutConfig
from .context import ReplContext

if t.TYPE_CHECKING:
    from bookscout.agents.mode import StreamChunk
    from bookscout.books.store import Book
    from bookscout.repl.session_manager import Session

# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------

_COMMANDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("back", "Return to the book list", ("chat", "session_select", "index_select", "builder_select")),
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
    ("rm", "Delete a session: :rm <name> | :rm :current", ("chat", "session_select")),
    ("session new", "Create a new session: :session new <name>", ("chat", "session_select")),
    ("session list", "List sessions for current book", ("chat",)),
    ("usage", "Show rate-limit usage stats", ("chat",)),
    ("verbose", "Toggle verbose tool output", ("chat",)),
    ("new", "Create a new session for the current book", ("session_select",)),
]


# ---------------------------------------------------------------------------
# OutputBuffer -- rich to ANSI to prompt_toolkit pipeline
# ---------------------------------------------------------------------------


class OutputBuffer:
    """Accumulates rich-rendered output for the prompt_toolkit output window."""

    def __init__(self, width: int = 120) -> None:
        self._ansi_parts: list[str] = []
        self._width = width
        self._console = Console(
            file=io.StringIO(),
            force_terminal=True,
            width=width,
            color_system="truecolor",
            legacy_windows=False,
        )

    def print(self, renderable: t.Any) -> None:
        """Render a rich object and append to the buffer."""
        buf = io.StringIO()
        self._console.file = buf
        self._console.print(renderable)
        self._ansi_parts.append(buf.getvalue())

    def print_text(self, text: str, style: str = "") -> None:
        """Append plain text with an optional rich style."""
        self.print(Text(text, style=style))

    def clear(self) -> None:
        """Clear all output."""
        self._ansi_parts.clear()

    def get_formatted_text(self) -> t.Any:
        """Return prompt_toolkit FormattedText for all stored output."""
        full = "".join(self._ansi_parts)
        return to_formatted_text(ANSI(full))


# ---------------------------------------------------------------------------
# SimpleTui
# ---------------------------------------------------------------------------


class SimpleTui:
    """Full-screen TUI using prompt_toolkit Layout + rich rendering."""

    def __init__(
        self,
        config: BookScoutConfig,
        *,
        initial_book_id: str | None = None,
        resume_session_id: str | None = None,
    ) -> None:
        self._config = config
        self._initial_book_id = initial_book_id
        self._resume_session_id = resume_session_id
        self._ctx: ReplContext | None = None

        # State
        self.phase: str = "select"
        self._books: list[Book] = []
        self._selected_book: Book | None = None
        self._session_id: str | None = None
        self._current_session: Session | None = None
        self._verbose_tools: bool = False
        self._chat_busy: bool = False

        # Select phase
        self._book_focus_idx: int = 0

        # Session select phase
        self._session_list: list[Session] = []
        self._session_focus_idx: int = 0
        self._session_select_cross_book: bool = False

        # Index select phase
        self._selected_index_types: set[str] = set()
        self._index_focus_idx: int = 0
        self._available_index_types: list[str] = []

        # Builder select phase
        self._selected_builder: str = "rule"
        self._builder_focus_idx: int = 0
        self._compile_source: str = ""

        # Compile phase
        self._pending_task_id: str | None = None

        # Streaming
        self._streaming_buffer: list[str] = []
        self._streaming_started: bool = False

        # Status
        self._status_text: str = ""

        # Pending async task (from input accept handler)
        self._pending_task: asyncio.Task[t.Any] | None = None

        # Output buffer
        self._output = OutputBuffer()

        # Input buffer
        self._input_buffer = Buffer(
            multiline=False,
            completer=self._make_completer(),
            history=FileHistory(str(pathlib.Path(config.resolved_workdir) / ".prompt_history")),
            accept_handler=self._on_input_accept,
        )

        # Build layout + application
        self._app = self._build_app()

    # -- Layout + Application ---------------------------------------------------

    def _make_completer(self) -> WordCompleter:
        """Create a phase-aware command completer."""
        return WordCompleter(
            [f":{cmd}" for cmd, _, _ in _COMMANDS],
            ignore_case=True,
            sentence=True,
        )

    def _build_app(self) -> Application[t.Any]:
        """Build the prompt_toolkit Application with Layout."""
        # Output window -- takes all remaining space
        output_control = FormattedTextControl(text=self._output.get_formatted_text)
        self._output_window = Window(
            content=output_control,
            wrap_lines=True,
        )

        # Status bar
        self._status_control = FormattedTextControl(text=self._get_status_text)
        status_window = Window(
            content=self._status_control,
            height=1,
            style="class:status-bar",
        )

        # Input line
        input_control = BufferControl(buffer=self._input_buffer)
        input_window = Window(
            content=input_control,
            height=1,
            style="class:input-line",
        )

        # Root layout
        root = HSplit([
            self._output_window,
            status_window,
            input_window,
        ])
        layout = Layout(root, focused_element=input_control)

        # Key bindings
        kb = self._build_key_bindings()

        # Style
        style = Style.from_dict({
            "status-bar": "reverse",
            "input-line": "",
        })

        return Application(
            layout=layout,
            key_bindings=kb,
            style=style,
            full_screen=True,
            refresh_interval=0.2,
        )

    def _get_status_text(self) -> list[tuple[str, str]]:
        """Return formatted text for the status bar."""
        phase_labels = {
            "select": "BookScout",
            "session_select": "Sessions",
            "index_select": "Indexes",
            "builder_select": "Builder",
            "chat": self._selected_book.title if self._selected_book else "Chat",
            "compile": "Compiling",
        }
        label = phase_labels.get(self.phase, self.phase)
        busy = " ..." if self._chat_busy else ""
        return [("class:status-bar", f" {label}{busy} | {self._status_text}")]

    def _build_key_bindings(self) -> KeyBindings:
        """Build phase-aware key bindings."""
        kb = KeyBindings()

        @Condition
        def in_select() -> bool:
            return self.phase == "select"

        @Condition
        def in_session_select() -> bool:
            return self.phase == "session_select"

        @Condition
        def in_index_select() -> bool:
            return self.phase == "index_select"

        @Condition
        def in_builder_select() -> bool:
            return self.phase == "builder_select"

        @Condition
        def in_chat() -> bool:
            return self.phase == "chat"

        # Ctrl-Q: exit from any phase
        @kb.add("c-q")
        def _exit(event: t.Any) -> None:
            event.app.exit()

        # Up: move focus up in list phases
        @kb.add("up", filter=in_select)
        def _select_up(_event: t.Any) -> None:
            if self._book_focus_idx > 0:
                self._book_focus_idx -= 1
                self._render_books()

        @kb.add("up", filter=in_session_select)
        def _session_up(_event: t.Any) -> None:
            if self._session_focus_idx > 0:
                self._session_focus_idx -= 1
                self._render_sessions()

        @kb.add("up", filter=in_index_select)
        def _index_up(_event: t.Any) -> None:
            if self._index_focus_idx > 0:
                self._index_focus_idx -= 1
                self._render_index_select()

        @kb.add("up", filter=in_builder_select)
        def _builder_up(_event: t.Any) -> None:
            if self._builder_focus_idx > 0:
                self._builder_focus_idx -= 1
                self._render_builder_select()

        # Down: move focus down in list phases
        @kb.add("down", filter=in_select)
        def _select_down(_event: t.Any) -> None:
            if self._book_focus_idx < len(self._books) - 1:
                self._book_focus_idx += 1
                self._render_books()

        @kb.add("down", filter=in_session_select)
        def _session_down(_event: t.Any) -> None:
            if self._session_focus_idx < len(self._session_list) - 1:
                self._session_focus_idx += 1
                self._render_sessions()

        @kb.add("down", filter=in_index_select)
        def _index_down(_event: t.Any) -> None:
            if self._index_focus_idx < len(self._available_index_types) - 1:
                self._index_focus_idx += 1
                self._render_index_select()

        @kb.add("down", filter=in_builder_select)
        def _builder_down(_event: t.Any) -> None:
            rows = self._builder_rows()
            if self._builder_focus_idx < len(rows) - 1:
                self._builder_focus_idx += 1
                self._render_builder_select()

        # j/k vim-style in select/session_select
        @kb.add("j", filter=in_select)
        def _select_j(_event: t.Any) -> None:
            if self._book_focus_idx < len(self._books) - 1:
                self._book_focus_idx += 1
                self._render_books()

        @kb.add("k", filter=in_select)
        def _select_k(_event: t.Any) -> None:
            if self._book_focus_idx > 0:
                self._book_focus_idx -= 1
                self._render_books()

        @kb.add("j", filter=in_session_select)
        def _session_j(_event: t.Any) -> None:
            if self._session_focus_idx < len(self._session_list) - 1:
                self._session_focus_idx += 1
                self._render_sessions()

        @kb.add("k", filter=in_session_select)
        def _session_k(_event: t.Any) -> None:
            if self._session_focus_idx > 0:
                self._session_focus_idx -= 1
                self._render_sessions()

        # Space: toggle in index_select
        @kb.add("space", filter=in_index_select)
        def _index_space(_event: t.Any) -> None:
            if self._available_index_types:
                idx_type = self._available_index_types[self._index_focus_idx]
                if idx_type in self._selected_index_types:
                    self._selected_index_types.discard(idx_type)
                else:
                    self._selected_index_types.add(idx_type)
                self._render_index_select()

        # Page Up/Down: scroll output in chat
        @kb.add("pageup", filter=in_chat)
        def _chat_pageup(event: t.Any) -> None:
            event.app.layout.current_window.vertical_scroll(-10)

        @kb.add("pagedown", filter=in_chat)
        def _chat_pagedown(event: t.Any) -> None:
            event.app.layout.current_window.vertical_scroll(10)

        return kb

    def _on_input_accept(self, buf: Buffer) -> None:
        """Handle Enter key in the input buffer."""
        text = buf.text
        buf.text = ""
        if text:
            self._pending_task = asyncio.ensure_future(self._dispatch_input(text))

    async def _dispatch_input(self, text: str) -> None:
        """Dispatch user input based on the current phase."""
        low = text.lower().strip()
        if not low:
            return

        if self.phase == "select":
            await self._handle_select_input(text)
        elif self.phase == "session_select":
            await self._handle_session_select_input(text)
        elif self.phase == "index_select":
            await self._handle_index_select_input(text)
        elif self.phase == "builder_select":
            await self._handle_builder_select_input(text)
        elif self.phase == "chat":
            await self._handle_chat_input(text)

    # -- Lifecycle --------------------------------------------------------------

    async def run(self) -> None:
        """Main entry point -- start context and run the application."""
        self._ctx = ReplContext(config=self._config)
        await self._ctx.startup()
        try:
            if self._initial_book_id:
                await self._jump_to_book(self._initial_book_id)
            elif self._resume_session_id:
                await self._jump_to_resume(self._resume_session_id)
            else:
                await self._refresh_books()
                self._render_books()
                self._set_status(f"{len(self._books)} book(s)")

            await self._app.run_async()
        finally:
            await self._ctx.shutdown()

    # -- Helpers ----------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        """Update the status bar text."""
        self._status_text = text

    def _invalidate(self) -> None:
        """Trigger a UI re-render."""
        self._app.invalidate()

    async def _refresh_books(self) -> None:
        """Refresh the book list from the store."""
        if self._ctx is None:
            return
        self._books = await self._ctx.list_books()
        self._book_focus_idx = 0

    # -- Phase: select ----------------------------------------------------------

    def _render_books(self) -> None:
        """Render the book list in the output area."""
        self._output.clear()
        self._output.print(Rule("BookScout", style="bold white"))
        if not self._books:
            self._output.print_text("  No books yet. Use :compile <path> to add one.", style="dim")
        else:
            for i, book in enumerate(self._books):
                marker = ">" if i == self._book_focus_idx else " "
                indexes = ", ".join(book.indexes) if book.indexes else "no indexes"
                if i == self._book_focus_idx:
                    self._output.print_text(f"  {marker} {i + 1}. {book.title}  ({indexes})", style="bold")
                else:
                    self._output.print_text(f"  {marker} {i + 1}. {book.title}  ({indexes})", style="dim")
        self._output.print_text("")
        self._output.print_text("  Up/Down j/k navigate  Enter select  :compile <path>  :resume", style="dim")
        self._invalidate()

    async def _handle_select_input(self, text: str) -> None:
        """Handle input in the select phase."""
        low = text.lower().strip()
        if low.startswith(":"):
            await self._handle_select_command(text)
            return
        try:
            idx = int(low) - 1
        except ValueError:
            self._set_status("Enter a number or :command")
            self._invalidate()
            return
        if 0 <= idx < len(self._books):
            book = self._books[idx]
            await self._enter_session_select(book)
        else:
            self._set_status(f"Invalid: {idx + 1} (1-{len(self._books)})")
            self._invalidate()

    async def _handle_select_command(self, text: str) -> None:
        """Handle :command in select phase."""
        low = text.lower().strip()
        if low in (":q", ":quit", ":exit"):
            self._app.exit()
            return
        if low.startswith(":compile ") or low.startswith(":c "):
            source = text.split(None, 1)[1].strip() if len(text.split()) > 1 else ""
            if not source:
                self._set_status("Usage: :compile <path>")
                self._invalidate()
                return
            await self._start_compile(source)
            return
        if low.startswith(":delete ") or low.startswith(":d "):
            parts = text.split()
            if len(parts) < 2:
                self._set_status("Usage: :delete N")
                self._invalidate()
                return
            try:
                idx = int(parts[1]) - 1
            except ValueError:
                self._set_status("Usage: :delete N")
                self._invalidate()
                return
            if 0 <= idx < len(self._books):
                await self._delete_book(self._books[idx].id)
                self._render_books()
            return
        if low.startswith(":addindex "):
            await self._handle_addindex(text)
            return
        if low.startswith(":rmindex "):
            await self._handle_rmindex(text)
            return
        if low == ":resume":
            await self._enter_cross_book_session_select()
            return
        self._set_status(f"Unknown: {text}")
        self._invalidate()

    # -- Phase: session_select --------------------------------------------------

    async def _enter_session_select(self, book: Book) -> None:
        """Enter session selection for a book."""
        self._selected_book = book
        assert self._ctx is not None
        sessions = await self._ctx.session_manager.list_by_book(book.id)
        if not sessions:
            await self._auto_create_session(book)
            return
        self._session_list = sessions
        self._session_focus_idx = 0
        self._session_select_cross_book = False
        self.phase = "session_select"
        self._render_sessions()

    async def _enter_cross_book_session_select(self) -> None:
        """Enter session selection across all books."""
        assert self._ctx is not None
        sessions = await self._ctx.session_manager.list_all()
        if not sessions:
            self._set_status("No sessions to resume")
            self._invalidate()
            return
        self._session_list = sessions
        self._session_focus_idx = 0
        self._session_select_cross_book = True
        self.phase = "session_select"
        self._render_sessions()

    def _render_sessions(self) -> None:
        """Render the session list in the output area."""
        self._output.clear()
        label = (
            "All sessions"
            if self._session_select_cross_book
            else f"Sessions for {self._selected_book.title if self._selected_book else '?'}"
        )
        self._output.print(Rule(label, style="bold white"))
        for i, s in enumerate(self._session_list):
            marker = ">" if i == self._session_focus_idx else " "
            if i == self._session_focus_idx:
                self._output.print_text(f"  {marker} {i + 1}. {s.name}  ({s.kind}, {s.turn_count} turns)", style="bold")
            else:
                self._output.print_text(f"  {marker} {i + 1}. {s.name}  ({s.kind}, {s.turn_count} turns)", style="dim")
        self._output.print_text("")
        self._output.print_text("  Up/Down j/k navigate  Enter select  :new <name>  :rm  :back", style="dim")
        self._set_status(f"{len(self._session_list)} session(s)")
        self._invalidate()

    async def _handle_session_select_input(self, text: str) -> None:
        """Handle input in session_select phase."""
        low = text.lower().strip()
        if low.startswith(":"):
            await self._handle_session_command(text)
            return
        try:
            idx = int(low) - 1
        except ValueError:
            self._set_status("Enter a number or :command")
            self._invalidate()
            return
        if 0 <= idx < len(self._session_list):
            session = self._session_list[idx]
            await self._enter_chat(session)
        else:
            self._set_status(f"Invalid: {idx + 1}")
            self._invalidate()

    async def _handle_session_command(self, text: str) -> None:
        """Handle :command in session_select phase."""
        low = text.lower().strip()
        if low in (":q", ":quit", ":exit"):
            self._app.exit()
            return
        if low in (":back", ":select"):
            self.phase = "select"
            await self._refresh_books()
            self._render_books()
            return
        if low.startswith(":session new ") or low.startswith(":new "):
            parts = text.split(None, 2)
            name = parts[2].strip() if len(parts) > 2 else None
            if not name:
                suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
                name = (
                    f"{(self._selected_book.title or 'untitled')[:20]}-{suffix}"
                    if self._selected_book
                    else f"session-{suffix}"
                )
            assert self._ctx is not None and self._selected_book is not None
            session = await self._ctx.session_manager.create(book_id=self._selected_book.id, name=name, kind="chat")
            await self._enter_chat(session)
            return
        if low == ":rm":
            if self._session_list and 0 <= self._session_focus_idx < len(self._session_list):
                session = self._session_list[self._session_focus_idx]
                assert self._ctx is not None
                await self._ctx.delete_session(session.session_id)
                self._set_status(f"Deleted: {session.name}")
                if self._session_select_cross_book:
                    self._session_list = await self._ctx.session_manager.list_all()
                elif self._selected_book:
                    self._session_list = await self._ctx.session_manager.list_by_book(self._selected_book.id)
                if not self._session_list:
                    await self._refresh_books()
                    self.phase = "select"
                    self._render_books()
                    return
                self._session_focus_idx = min(self._session_focus_idx, len(self._session_list) - 1)
                self._render_sessions()
            return
        self._set_status(f"Unknown: {text}")
        self._invalidate()

    # -- Phase: index_select ----------------------------------------------------

    async def _enter_index_select(self, source_path: str) -> None:
        """Enter index selection for a new compile."""
        assert self._ctx is not None
        self._compile_source = source_path
        self._selected_index_types = set()
        self._index_focus_idx = 0
        self._available_index_types = [p.index_type for p in self._ctx.registry.all()]
        self.phase = "index_select"
        self._render_index_select()

    def _render_index_select(self) -> None:
        """Render the index selection checkboxes."""
        self._output.clear()
        self._output.print(Rule("Select Indexes", style="bold white"))
        for i, idx_type in enumerate(self._available_index_types):
            marker = ">" if i == self._index_focus_idx else " "
            checked = "[x]" if idx_type in self._selected_index_types else "[ ]"
            if i == self._index_focus_idx:
                self._output.print_text(f"  {marker} {checked}  {idx_type}", style="bold")
            else:
                self._output.print_text(f"  {marker} {checked}  {idx_type}", style="dim")
        self._output.print_text("")
        self._output.print_text("  Up/Down navigate  Space toggle  :go confirm  :back cancel", style="dim")
        self._set_status(f"Selected: {', '.join(sorted(self._selected_index_types)) or 'none'}")
        self._invalidate()

    async def _handle_index_select_input(self, text: str) -> None:
        """Handle input in index_select phase."""
        low = text.lower().strip()
        if low in (":go", ":ok", ":next", ""):
            if self._selected_index_types:
                self._enter_builder_select()
            else:
                self._set_status("Select at least one index")
                self._invalidate()
            return
        if low in (":back", ":cancel", ":select"):
            self.phase = "select"
            await self._refresh_books()
            self._render_books()
            return
        if low in (":q", ":quit", ":exit"):
            self._app.exit()
            return
        self._set_status(f"Unknown: {text}")
        self._invalidate()

    # -- Phase: builder_select --------------------------------------------------

    def _enter_builder_select(self) -> None:
        """Enter builder selection after indexes are chosen."""
        assert self._ctx is not None
        self._selected_builder = self._ctx.default_builder
        self._builder_focus_idx = 0
        self.phase = "builder_select"
        self._render_builder_select()

    def _builder_rows(self) -> list[tuple[str, str, str]]:
        """Return available builder options: (key, label, description)."""
        rows: list[tuple[str, str, str]] = [
            ("rule", "Rule", "Fast, deterministic heuristics (default)"),
        ]
        assert self._ctx is not None
        if self._ctx.has_llm_builder:
            rows.append(("llm", "LLM", "Tool-driven outline; slower, higher quality"))
        return rows

    def _render_builder_select(self) -> None:
        """Render the builder selection radio list."""
        rows = self._builder_rows()
        self._output.clear()
        self._output.print(Rule("Select Builder", style="bold white"))
        for i, (key, label, desc) in enumerate(rows):
            marker = ">" if i == self._builder_focus_idx else " "
            checked = "(*)" if key == self._selected_builder else "( )"
            if i == self._builder_focus_idx:
                self._output.print_text(f"  {marker} {checked}  {label}    {desc}", style="bold")
            else:
                self._output.print_text(f"  {marker} {checked}  {label}    {desc}", style="dim")
        self._output.print_text("")
        self._output.print_text("  Up/Down navigate  Enter select  :go build  :back cancel", style="dim")
        self._set_status(f"Builder: {self._selected_builder}")
        self._invalidate()

    async def _handle_builder_select_input(self, text: str) -> None:
        """Handle input in builder_select phase."""
        low = text.lower().strip()
        if low in (":go", ":ok", ""):
            await self._start_compile(
                self._compile_source,
                index_types=self._selected_index_types,
                builder=self._selected_builder,
            )
            return
        if low in (":back", ":cancel"):
            self.phase = "index_select"
            self._render_index_select()
            return
        if low in (":q", ":quit", ":exit"):
            self._app.exit()
            return
        rows = self._builder_rows()
        try:
            idx = int(low) - 1
            if 0 <= idx < len(rows):
                self._selected_builder = rows[idx][0]
                self._render_builder_select()
                return
        except ValueError:
            pass
        for key, label, _ in rows:
            if low == key.lower() or low == label.lower():
                self._selected_builder = key
                self._render_builder_select()
                return
        self._set_status(f"Unknown: {text}")
        self._invalidate()

    # -- Phase: chat ------------------------------------------------------------

    async def _enter_chat(self, session: Session) -> None:
        """Enter chat phase for a session."""
        self._session_id = session.session_id
        self._current_session = session
        self.phase = "chat"
        await self._load_chat_history(session)
        self._set_status(session.name)
        self._invalidate()

    async def _load_chat_history(self, session: Session) -> None:
        """Load and render existing chat history."""
        assert self._ctx is not None
        messages = await self._ctx.session_manager.load_messages(session.session_id)
        if not messages:
            self._output.clear()
            return
        self._output.clear()
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                escaped = content.replace("\n", "\n> ")
                self._output.print(Markdown(f"> {escaped}"))
                self._output.print(Rule(style="dim"))
            elif role == "assistant":
                self._output.print(Markdown(content))
                self._output.print_text("")

    async def _handle_chat_input(self, text: str) -> None:
        """Handle input in chat phase."""
        low = text.lower().strip()
        if low.startswith(":"):
            await self._handle_chat_command(text)
            return
        await self._stream_chat(text)

    async def _stream_chat(self, user_input: str) -> None:
        """Send user input and stream the response."""
        assert self._ctx is not None and self._selected_book is not None and self._session_id is not None

        escaped = user_input.replace("\n", "\n> ")
        self._output.print(Markdown(f"> {escaped}"))
        self._output.print(Rule(style="dim"))
        self._invalidate()

        self._chat_busy = True
        self._streaming_buffer = []
        self._streaming_started = False
        response_text_parts: list[str] = []
        self._set_status("thinking...")
        self._invalidate()

        try:
            stream = self._ctx.chat(
                self._selected_book.id,
                self._session_id,
                user_input,
            )
            async for chunk in stream:
                self._handle_chunk(chunk, response_text_parts)
            self._flush_streaming()

        except Exception as e:
            self._flush_streaming()
            self._output.print_text(f"\nERROR: {e}\n", style="bold red")
            self._invalidate()
        finally:
            self._chat_busy = False
            self._set_status(self._selected_book.title or "(untitled)")
            self._invalidate()

            # Persist messages
            if self._current_session and self._ctx:
                response_text = "".join(response_text_parts)
                mgr = self._ctx.session_manager
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

    def _handle_chunk(self, chunk: StreamChunk, response_parts: list[str]) -> None:
        """Handle a streaming chunk and update the output."""
        if chunk.kind == "text":
            delta = chunk.data if isinstance(chunk.data, str) else str(chunk.data)
            response_parts.append(delta)
            if not self._streaming_started:
                self._streaming_started = True
            self._streaming_buffer.append(delta)
            joined = "".join(self._streaming_buffer)
            if "\n" in joined:
                head, _, tail = joined.rpartition("\n")
                self._streaming_buffer = [tail]
                self._output.print(Markdown(head))
                self._invalidate()

        elif chunk.kind == "tool_call":
            self._flush_streaming()
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            self._output.print_text(f"  -> {name}", style="dim")
            self._invalidate()

        elif chunk.kind == "tool_result":
            self._flush_streaming()
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            summary = data.get("summary", "")
            stats = data.get("retrieval_stats") or {}
            stats_str = ", ".join(f"{k}={v}" for k, v in stats.items())
            line = f"  <- {name}"
            if summary:
                line += f"  {summary}"
            if stats_str:
                line += f"  [{stats_str}]"
            self._output.print_text(line, style="dim")
            if self._verbose_tools:
                self._render_verbose_tool(data)
            self._invalidate()

        elif chunk.kind == "status":
            data = chunk.data if isinstance(chunk.data, dict) else {}
            phase = data.get("phase", "")
            if phase == "auto_compacted":
                self._flush_streaming()
                self._output.print_text("  [auto-compacted]", style="dim italic")
                self._invalidate()
            elif phase == "retry":
                attempt = data.get("attempt", "?")
                max_r = data.get("max_retries", "?")
                err_short = str(data.get("error", ""))[:60]
                self._set_status(f"LLM error, retrying ({attempt}/{max_r}): {err_short}")
                self._invalidate()

    def _flush_streaming(self) -> None:
        """Flush any remaining streaming text to the output buffer."""
        if not self._streaming_started:
            return
        text = "".join(self._streaming_buffer)
        self._streaming_buffer = []
        self._streaming_started = False
        if text:
            self._output.print(Markdown(text))
            self._invalidate()

    @staticmethod
    def _render_verbose_tool(data: dict) -> None:
        """Render full params and result (verbose mode)."""
        pass

    async def _handle_chat_command(self, text: str) -> None:
        """Handle :command in chat phase."""
        low = text.lower().strip()
        assert self._ctx is not None

        if low in (":q", ":quit", ":exit"):
            self._app.exit()
            return

        if low in (":back", ":select"):
            self._selected_book = None
            self._session_id = None
            self._current_session = None
            self.phase = "select"
            await self._refresh_books()
            self._render_books()
            return

        if low == ":clear":
            self._output.clear()
            self._invalidate()
            return

        if low == ":compact":
            self._set_status("Compacting...")
            self._invalidate()
            mode = self._ctx._modes.get(self._session_id or "")
            if mode is None:
                self._set_status("No active session to compact")
                self._invalidate()
                return
            summary = await mode.compact()
            if summary:
                self._output.print_text(f"  Compacted: {summary[:100]}", style="dim")
            self._set_status(self._selected_book.title if self._selected_book else "Chat")
            self._invalidate()
            return

        if low.startswith(":rename "):
            parts = text.split(None, 1)
            if len(parts) < 2:
                self._set_status("Usage: :rename <NAME>")
                self._invalidate()
                return
            new_name = parts[1].strip()
            if self._current_session:
                await self._ctx.session_manager.rename(self._current_session.session_id, new_name)
                self._current_session = await self._ctx.session_manager.get(self._current_session.session_id)
                self._set_status(f"Renamed to: {new_name}")
                self._invalidate()
            return

        if low.startswith(":rm"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                self._set_status("Usage: :rm <name> | :rm :current")
                self._invalidate()
                return
            target = parts[1].strip()
            mgr = self._ctx.session_manager
            if target == ":current":
                if not self._current_session:
                    self._set_status("No active session")
                    self._invalidate()
                    return
                sid = self._current_session.session_id
                await self._ctx.delete_session(sid)
                self._current_session = None
                self._session_id = None
                if self._selected_book:
                    sessions = await mgr.list_by_book(self._selected_book.id)
                    if sessions:
                        self._session_list = sessions
                        self._session_focus_idx = 0
                        self.phase = "session_select"
                        self._render_sessions()
                    else:
                        await self._refresh_books()
                        self.phase = "select"
                        self._render_books()
                return
            if not self._selected_book:
                self._set_status("No book selected")
                self._invalidate()
                return
            sessions = await mgr.list_by_book(self._selected_book.id)
            match = next((s for s in sessions if s.name == target), None)
            if match is None:
                self._set_status(f"Session not found: {target}")
                self._invalidate()
                return
            await self._ctx.delete_session(match.session_id)
            self._set_status(f"Deleted: {target}")
            self._invalidate()
            return

        if low.startswith(":session new "):
            parts = text.split(None, 2)
            name = parts[2].strip() if len(parts) > 2 else None
            if not name:
                suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
                name = (
                    f"{(self._selected_book.title or 'untitled')[:20]}-{suffix}"
                    if self._selected_book
                    else f"session-{suffix}"
                )
            if self._selected_book is None:
                self._set_status("No book selected")
                self._invalidate()
                return
            session = await self._ctx.session_manager.create(book_id=self._selected_book.id, name=name, kind="chat")
            await self._enter_chat(session)
            return

        if low == ":session list":
            if self._selected_book is None:
                self._set_status("No book selected")
                self._invalidate()
                return
            sessions = await self._ctx.session_manager.list_by_book(self._selected_book.id)
            self._output.print(Rule("Sessions", style="bold white"))
            for s in sessions:
                marker = "> " if self._current_session and s.session_id == self._current_session.session_id else "  "
                self._output.print_text(f"{marker}{s.name}  ({s.kind}, {s.turn_count} turns)", style="dim")
            self._invalidate()
            return

        if low == ":usage":
            llm = self._ctx.llm
            if llm is None:
                self._output.print_text("LLM not configured.", style="dim")
                self._invalidate()
                return
            usage = await llm.get_rate_limit_usage()
            rl_mode = self._ctx._config.ratelimit.mode
            if usage is None:
                self._output.print_text("\nRate Limit: off (no limits configured)\n", style="dim")
            else:
                mode_label = "requests" if rl_mode == "requests" else "tokens"
                unit = "req" if rl_mode == "requests" else "tok"
                self._output.print_text(f"\nRate Limit: {mode_label} mode", style="bold")
                for wname, info in usage.items():
                    limit = info["limit"]
                    used = info["requests"] if rl_mode == "requests" else info["tokens_used"]
                    if limit == 0:
                        self._output.print_text(f"  {wname}: {used} {unit} (unlimited)", style="dim")
                    else:
                        remaining = max(0, limit - used)
                        self._output.print_text(
                            f"  {wname}: {used:,} / {limit:,} {unit}  ({remaining:,} remaining)", style="dim"
                        )
                self._output.print_text("")
            self._invalidate()
            return

        if low == ":verbose":
            self._verbose_tools = not self._verbose_tools
            self._set_status(f"Verbose tools: {'on' if self._verbose_tools else 'off'}")
            self._invalidate()
            return

        if low.startswith(":addindex ") or low.startswith(":addidx "):
            await self._handle_addindex(text)
            return

        if low.startswith(":rmindex ") or low.startswith(":rmidx "):
            await self._handle_rmindex(text)
            return

        self._set_status(f"Unknown: {text}")
        self._invalidate()

    # -- Phase: compile ---------------------------------------------------------

    async def _start_compile(
        self,
        source_path: str,
        *,
        index_types: set[str] | None = None,
        builder: str = "rule",
    ) -> None:
        """Start a compile task."""
        assert self._ctx is not None
        self.phase = "compile"
        self._output.clear()
        self._output.print_text(f"Compiling: {source_path}", style="dim")
        self._set_status(f"compiling: {pathlib.Path(source_path).name}")
        self._invalidate()
        try:
            task_id = await self._ctx.compile(source_path, index_types=index_types, builder=builder)
            self._pending_task_id = task_id
            progress = None
            while True:
                progress = self._ctx.get_task_progress(task_id)
                if progress is None:
                    break
                if progress.status in ("succeeded", "failed"):
                    break
                pct = int(progress.percentage)
                self._output.print_text(
                    f"  {progress.stage}: {pct}% ({progress.processed}/{progress.total})", style="dim"
                )
                self._invalidate()
                await asyncio.sleep(1)
            if progress is not None and progress.status == "failed":
                self._output.print_text(f"Compile failed: {progress.error}", style="bold red")
            else:
                self._output.print_text("Compile complete.", style="dim")
        except Exception as e:
            self._output.print_text(f"Compile error: {e}", style="bold red")
        finally:
            await self._refresh_books()
            self.phase = "select"
            self._render_books()

    # -- More helpers -----------------------------------------------------------

    async def _auto_create_session(self, book: Book) -> None:
        """Auto-create a session and enter chat."""
        assert self._ctx is not None
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
        name = f"{(book.title or 'untitled')[:20]}-{suffix}"
        session = await self._ctx.session_manager.create(book_id=book.id, name=name, kind="chat")
        await self._enter_chat(session)

    async def _jump_to_book(self, book_id: str) -> None:
        """Jump directly to a book (for --book flag)."""
        assert self._ctx is not None
        await self._refresh_books()
        book = next((b for b in self._books if b.id == book_id), None)
        if book is None:
            self._output.print_text(f"Book not found: {book_id}", style="bold red")
            await self._refresh_books()
            self._render_books()
            return
        await self._enter_session_select(book)

    async def _jump_to_resume(self, session_id: str) -> None:
        """Jump directly to a session (for --resume flag)."""
        assert self._ctx is not None
        session = await self._ctx.session_manager.get(session_id)
        if session is None:
            self._output.print_text(f"Session not found: {session_id}", style="bold red")
            await self._refresh_books()
            self._render_books()
            return
        await self._refresh_books()
        book = next((b for b in self._books if b.id == session.book_id), None)
        if book is None:
            self._output.print_text(f"Book not found for session: {session.book_id}", style="bold red")
            await self._refresh_books()
            self._render_books()
            return
        self._selected_book = book
        await self._enter_chat(session)

    async def _delete_book(self, book_id: str) -> None:
        """Delete a book."""
        assert self._ctx is not None
        for idx in list(self._selected_book.indexes if self._selected_book else []):
            with contextlib.suppress(Exception):
                await self._ctx.remove_index(book_id, idx)
        self._set_status(f"Deleted book: {book_id}")
        await self._refresh_books()

    async def _handle_addindex(self, text: str) -> None:
        """Handle :addindex command."""
        parts = text.split()
        assert self._ctx is not None

        if self.phase == "select":
            if len(parts) < 3 or not parts[1].isdigit():
                self._set_status("Usage: :addindex N <type>")
                self._invalidate()
                return
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(self._books)):
                self._set_status(f"Invalid book number: {idx + 1}")
                self._invalidate()
                return
            book = self._books[idx]
            idx_type = parts[2].lower()
        else:
            if len(parts) < 2:
                self._set_status("Usage: :addindex <type>")
                self._invalidate()
                return
            if self._selected_book is None:
                self._set_status("No book selected")
                self._invalidate()
                return
            book = self._selected_book
            idx_type = parts[1].lower()

        provider = self._ctx.registry.by_type(idx_type)
        if provider is None:
            self._set_status(f"Unknown index: {idx_type}")
            self._invalidate()
            return
        built = set(book.indexes)
        if idx_type in built:
            self._set_status(f"{idx_type} already built")
            self._invalidate()
            return
        self._set_status(f"Building index: {idx_type}...")
        self._invalidate()
        try:
            await self._ctx.add_index(book.id, {idx_type})
            self._set_status(f"Index built: {idx_type}")
            await self._refresh_books()
            if self.phase == "select":
                self._render_books()
            else:
                self._invalidate()
        except Exception as e:
            self._output.print_text(f"Error: {e}", style="bold red")
            self._invalidate()

    async def _handle_rmindex(self, text: str) -> None:
        """Handle :rmindex command."""
        parts = text.split()
        assert self._ctx is not None

        if self.phase == "select":
            if len(parts) < 3 or not parts[1].isdigit():
                self._set_status("Usage: :rmindex N <type>")
                self._invalidate()
                return
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(self._books)):
                self._set_status(f"Invalid book number: {idx + 1}")
                self._invalidate()
                return
            book = self._books[idx]
            idx_type = parts[2].lower()
        else:
            if len(parts) < 2:
                self._set_status("Usage: :rmindex <type>")
                self._invalidate()
                return
            if self._selected_book is None:
                self._set_status("No book selected")
                self._invalidate()
                return
            book = self._selected_book
            idx_type = parts[1].lower()

        built = set(book.indexes)
        if idx_type not in built:
            self._set_status(f"{idx_type} not built for this book")
            self._invalidate()
            return
        try:
            await self._ctx.remove_index(book.id, idx_type)
            self._set_status(f"Removed index: {idx_type}")
            await self._refresh_books()
            if self.phase == "select":
                self._render_books()
            else:
                self._invalidate()
        except Exception as e:
            self._output.print_text(f"Error: {e}", style="bold red")
            self._invalidate()


__all__ = ["SimpleTui"]
