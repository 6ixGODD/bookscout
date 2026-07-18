"""Minimal TUI using prompt_toolkit (input) + rich (output).

Replaces the Textual-based TUI with a lightweight alternative:
- prompt_toolkit handles input, command completion, and history.
- rich handles all output rendering (markdown, tables, dim tool calls).
- No mouse support, no CSS, no widget tree — pure keyboard-driven.

Phases: select → session_select → chat (primary).
Also: index_select, builder_select, compile (transient).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import pathlib
import random
import string
import typing as t

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer
from prompt_toolkit.completion import Completion
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
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
# Internal exception for clean phase exit
# ---------------------------------------------------------------------------


class _PhaseExitError(Exception):
    """Raised to exit the TUI cleanly."""


# ---------------------------------------------------------------------------
# Command registry — same commands as the Textual TUI
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


class _CommandCompleter(Completer):
    """Completes :commands based on the current phase."""

    def __init__(self, tui: SimpleTui) -> None:
        self._tui = tui

    def get_completions(self, document: t.Any, complete_event: t.Any) -> t.Iterator[Completion]:
        text = document.text_before_cursor
        if not text.startswith(":"):
            return
        query = text.lstrip(":").lower()
        phase = self._tui.phase
        for cmd, desc, phases in _COMMANDS:
            if phase not in phases:
                continue
            if not query or query in cmd.lower():
                yield Completion(
                    f":{cmd} ",
                    start_position=-len(text),
                    display=f":{cmd}",
                    display_meta=desc,
                )


# ---------------------------------------------------------------------------
# SimpleTui
# ---------------------------------------------------------------------------


class SimpleTui:
    """Minimal TUI using prompt_toolkit + rich.

    Usage::

        tui = SimpleTui(config)
        await tui.run()
    """

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

        # Builder select phase
        self._selected_builder: str = "rule"

        # Compile phase
        self._compile_source: str = ""
        self._pending_task_id: str | None = None

        # Rich console
        self._console = Console()

        # prompt_toolkit session
        history_path = pathlib.Path(config.resolved_workdir) / ".prompt_history"
        self._prompt: PromptSession[str] = PromptSession(
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=_CommandCompleter(self),
        )

    # ── Lifecycle ──────────────────────────────────────────────

    async def run(self) -> None:
        """Main entry point — start context and enter phase loop."""
        self._ctx = ReplContext(config=self._config)
        await self._ctx.startup()
        try:
            # Handle --book and --resume flags.
            if self._initial_book_id:
                await self._jump_to_book(self._initial_book_id)
            elif self._resume_session_id:
                await self._jump_to_resume(self._resume_session_id)
            else:
                await self._refresh_books()
                await self._phase_select()
        except _PhaseExitError:
            pass
        finally:
            await self._ctx.shutdown()

    # ── Phase: select ──────────────────────────────────────────

    async def _phase_select(self) -> None:
        """Book selection loop."""
        self.phase = "select"
        while True:
            self._render_books()
            text = await self._input("Select book")
            if text is None:
                return  # Ctrl-D
            low = text.lower().strip()
            if not low:
                continue
            if low.startswith(":"):
                if await self._handle_select_command(text):
                    return  # exit
                continue
            # Number selection
            try:
                idx = int(low) - 1
            except ValueError:
                self._console.print("[dim]Enter a number or :command[/dim]")
                continue
            if 0 <= idx < len(self._books):
                book = self._books[idx]
                await self._enter_session_select(book)
            else:
                self._console.print(f"[dim]Invalid: {idx + 1} (1-{len(self._books)})[/dim]")

    def _render_books(self) -> None:
        """Print numbered book list."""
        self._console.print()
        self._console.print(Rule("BookScout", style="bold white"))
        if not self._books:
            self._console.print("[dim]No books yet. Use :compile <path> to add one.[/dim]")
            return
        for i, book in enumerate(self._books):
            marker = "→" if i == self._book_focus_idx else " "
            indexes = ", ".join(book.indexes) if book.indexes else "no indexes"
            self._console.print(f"  {marker} [bold]{i + 1}.[/bold] {book.title}  [dim]({indexes})[/dim]")

    async def _handle_select_command(self, text: str) -> bool:
        """Handle command in select phase. Returns True if should exit."""
        low = text.lower().strip()
        if low in (":q", ":quit", ":exit"):
            return True
        if low.startswith(":compile ") or low.startswith(":c "):
            source = text.split(None, 1)[1].strip() if len(text.split()) > 1 else ""
            if not source:
                self._console.print("[dim]Usage: :compile <path>[/dim]")
                return False
            await self._start_compile(source)
            return False
        if low.startswith(":delete ") or low.startswith(":d "):
            parts = text.split()
            if len(parts) < 2:
                self._console.print("[dim]Usage: :delete N[/dim]")
                return False
            try:
                idx = int(parts[1]) - 1
            except ValueError:
                self._console.print("[dim]Usage: :delete N[/dim]")
                return False
            if 0 <= idx < len(self._books):
                await self._delete_book(self._books[idx].id)
            return False
        if low.startswith(":addindex "):
            await self._handle_addindex(text)
            return False
        if low.startswith(":rmindex "):
            await self._handle_rmindex(text)
            return False
        if low == ":resume":
            await self._enter_cross_book_session_select()
            return False
        self._console.print(f"[dim]Unknown command: {text}[/dim]")
        return False

    # ── Phase: session_select ──────────────────────────────────

    async def _enter_session_select(self, book: Book) -> None:
        """Enter session selection for a book."""
        self._selected_book = book
        assert self._ctx is not None
        sessions = await self._ctx.session_manager.list_by_book(book.id)
        if not sessions:
            # Auto-create a session and enter chat.
            await self._auto_create_session(book)
            return
        self._session_list = sessions
        self._session_focus_idx = 0
        self._session_select_cross_book = False
        self.phase = "session_select"
        await self._session_select_loop()

    async def _enter_cross_book_session_select(self) -> None:
        """Enter session selection across all books."""
        assert self._ctx is not None
        sessions = await self._ctx.session_manager.list_all()
        if not sessions:
            self._console.print("[dim]No sessions to resume.[/dim]")
            return
        self._session_list = sessions
        self._session_focus_idx = 0
        self._session_select_cross_book = True
        self.phase = "session_select"
        await self._session_select_loop()

    async def _session_select_loop(self) -> None:
        """Session selection loop."""
        while True:
            self._render_sessions()
            text = await self._input("Session")
            if text is None:
                return
            low = text.lower().strip()
            if not low:
                continue
            if low.startswith(":"):
                if await self._handle_session_command(text):
                    return
                continue
            try:
                idx = int(low) - 1
            except ValueError:
                self._console.print("[dim]Enter a number or :command[/dim]")
                continue
            if 0 <= idx < len(self._session_list):
                session = self._session_list[idx]
                await self._enter_chat(session)
                return
            self._console.print(f"[dim]Invalid: {idx + 1}[/dim]")

    def _render_sessions(self) -> None:
        """Print numbered session list."""
        self._console.print()
        label = (
            "All sessions"
            if self._session_select_cross_book
            else f"Sessions for {self._selected_book.title if self._selected_book else '?'}"
        )
        self._console.print(Rule(label, style="bold white"))
        for i, s in enumerate(self._session_list):
            marker = "→" if i == self._session_focus_idx else " "
            self._console.print(
                f"  {marker} [bold]{i + 1}.[/bold] {s.name}  [dim]({s.kind}, {s.turn_count} turns)[/dim]"
            )
        self._console.print("[dim]:new <name> or :rm to delete[/dim]")

    async def _handle_session_command(self, text: str) -> bool:
        """Handle command in session_select. Returns True if should exit."""
        low = text.lower().strip()
        if low in (":q", ":quit", ":exit"):
            return True
        if low in (":back", ":select"):
            self.phase = "select"
            await self._refresh_books()
            await self._phase_select()
            return True
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
            return True
        if low == ":rm":
            if self._session_list and 0 <= self._session_focus_idx < len(self._session_list):
                session = self._session_list[self._session_focus_idx]
                assert self._ctx is not None
                await self._ctx.delete_session(session.session_id)
                self._console.print(f"[dim]Deleted: {session.name}[/dim]")
                # Refresh list
                if self._session_select_cross_book:
                    self._session_list = await self._ctx.session_manager.list_all()
                elif self._selected_book:
                    self._session_list = await self._ctx.session_manager.list_by_book(self._selected_book.id)
                if not self._session_list:
                    await self._refresh_books()
                    self.phase = "select"
                    await self._phase_select()
                    return True
                self._session_focus_idx = min(self._session_focus_idx, len(self._session_list) - 1)
            return False
        self._console.print(f"[dim]Unknown command: {text}[/dim]")
        return False

    # ── Phase: chat ────────────────────────────────────────────

    async def _enter_chat(self, session: Session) -> None:
        """Enter chat phase for a session."""
        self._session_id = session.session_id
        self._current_session = session
        self.phase = "chat"

        # Load history.
        await self._load_chat_history(session)

        # Chat loop.
        await self._chat_loop()

    async def _load_chat_history(self, session: Session) -> None:
        """Load and render existing chat history."""
        assert self._ctx is not None
        messages = await self._ctx.session_manager.load_messages(session.session_id)
        if not messages:
            return
        self._console.print()
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "user":
                escaped = content.replace("\n", "\n> ")
                self._console.print(Markdown(f"> {escaped}"))
                self._console.print(Rule(style="dim"))
            elif role == "assistant":
                self._console.print(Markdown(content))
                self._console.print()

    async def _chat_loop(self) -> None:
        """Main chat input loop."""
        while True:
            try:
                text = await self._input("Chat")
            except (EOFError, KeyboardInterrupt):
                continue
            if text is None:
                continue
            if not text:
                continue
            low = text.lower().strip()

            # Commands
            if low.startswith(":"):
                if await self._handle_chat_command(text):
                    return  # phase changed
                continue

            # Normal chat
            await self._stream_chat(text)

    async def _stream_chat(self, user_input: str) -> None:
        """Send user input and stream the response."""
        assert self._ctx is not None and self._selected_book is not None and self._session_id is not None

        # Print user message.
        escaped = user_input.replace("\n", "\n> ")
        self._console.print()
        self._console.print(Markdown(f"> {escaped}"))
        self._console.print(Rule(style="dim"))

        self._chat_busy = True
        response_parts: list[str] = []

        try:
            stream = self._ctx.chat(
                self._selected_book.id,
                self._session_id,
                user_input,
            )
            async for chunk in stream:
                await self._render_chunk(chunk, response_parts)

            # Flush remaining text.
            if response_parts:
                full = "".join(response_parts)
                self._console.print(Markdown(full))
                self._console.print()

        except Exception as e:
            self._console.print(f"\n[bold red]ERROR:[/bold red] {e}\n")
        finally:
            self._chat_busy = False

    async def _render_chunk(self, chunk: StreamChunk, response_parts: list[str]) -> None:
        """Render a streaming chunk to the console."""
        if chunk.kind == "text":
            delta = chunk.data if isinstance(chunk.data, str) else str(chunk.data)
            response_parts.append(delta)
        elif chunk.kind == "tool_call":
            # Flush text so far, then print dim tool call.
            if response_parts:
                full = "".join(response_parts)
                self._console.print(Markdown(full))
                response_parts.clear()
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            self._console.print(Text(f"  → {name}", style="dim"))
        elif chunk.kind == "tool_result":
            if response_parts:
                full = "".join(response_parts)
                self._console.print(Markdown(full))
                response_parts.clear()
            data = chunk.data if isinstance(chunk.data, dict) else {}
            name = data.get("tool_name", "?")
            summary = data.get("summary", "")
            stats = data.get("retrieval_stats") or {}
            stats_str = ", ".join(f"{k}={v}" for k, v in stats.items())
            line = f"  ← {name}"
            if summary:
                line += f"  {summary}"
            if stats_str:
                line += f"  [{stats_str}]"
            self._console.print(Text(line, style="dim"))
            if self._verbose_tools:
                self._render_verbose_tool(data)
        elif chunk.kind == "status":
            data = chunk.data if isinstance(chunk.data, dict) else {}
            phase = data.get("phase", "")
            if phase == "auto_compacted":
                self._console.print("[dim italic][auto-compacted][/dim italic]")
            elif phase == "retry":
                attempt = data.get("attempt", "?")
                max_r = data.get("max_retries", "?")
                err_short = str(data.get("error", ""))[:60]
                self._console.print(Text(f"  ⚠ retry ({attempt}/{max_r}): {err_short}", style="dim yellow"))

    @staticmethod
    def _render_verbose_tool(data: dict) -> None:
        """Render full params and result (verbose mode)."""
        console = Console()
        args = data.get("arguments") or {}
        if args:
            try:
                args_json = json.dumps(args, indent=2, ensure_ascii=False)
            except (TypeError, ValueError):
                args_json = str(args)
            console.print(Text(f"    params: {args_json[:200]}", style="dim"))
        result_text = data.get("result_text", "")
        if result_text:
            console.print(Text(f"    result: {result_text[:200]}", style="dim"))

    async def _handle_chat_command(self, text: str) -> bool:
        """Handle :command in chat. Returns True if phase changed."""
        low = text.lower().strip()
        assert self._ctx is not None

        if low in (":q", ":quit", ":exit"):
            raise _PhaseExitError

        if low in (":back", ":select"):
            self._selected_book = None
            await self._refresh_books()
            self.phase = "select"
            # We return True to exit the chat loop, then the caller
            # will re-enter _phase_select via the main run() loop.
            return True

        if low == ":clear":
            # Just print a separator — can't really clear in prompt_toolkit.
            self._console.print(Rule(style="dim"))
            return False

        if low == ":compact":
            self._console.print("[dim]Compacting...[/dim]")
            mode = self._ctx._modes.get(self._session_id or "")
            if mode is None:
                self._console.print("[dim]No active session to compact.[/dim]")
                return False
            summary = await mode.compact()
            if summary:
                self._console.print(f"[dim]Compacted: {summary[:100]}[/dim]")
            return False

        if low.startswith(":rename "):
            parts = text.split(None, 1)
            if len(parts) < 2:
                self._console.print("[dim]Usage: :rename <NAME>[/dim]")
                return False
            new_name = parts[1].strip()
            if self._current_session:
                await self._ctx.session_manager.rename(self._current_session.session_id, new_name)
                self._current_session = await self._ctx.session_manager.get(self._current_session.session_id)
                self._console.print(f"[dim]Renamed to: {new_name}[/dim]")
            return False

        if low.startswith(":rm"):
            parts = text.split(None, 1)
            if len(parts) < 2:
                self._console.print("[dim]Usage: :rm <name> | :rm :current[/dim]")
                return False
            target = parts[1].strip()
            mgr = self._ctx.session_manager
            if target == ":current":
                if not self._current_session:
                    self._console.print("[dim]No active session.[/dim]")
                    return False
                sid = self._current_session.session_id
                await self._ctx.delete_session(sid)
                self._current_session = None
                self._session_id = None
                if self._selected_book:
                    sessions = await mgr.list_by_book(self._selected_book.id)
                    if sessions:
                        await self._enter_session_select(self._selected_book)
                    else:
                        await self._refresh_books()
                        self.phase = "select"
                    return True
            else:
                if not self._selected_book:
                    self._console.print("[dim]No book selected.[/dim]")
                    return False
                sessions = await mgr.list_by_book(self._selected_book.id)
                match = next((s for s in sessions if s.name == target), None)
                if match is None:
                    self._console.print(f"[dim]Session not found: {target}[/dim]")
                    return False
                await self._ctx.delete_session(match.session_id)
                self._console.print(f"[dim]Deleted: {target}[/dim]")
            return False

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
                self._console.print("[dim]No book selected.[/dim]")
                return False
            session = await self._ctx.session_manager.create(book_id=self._selected_book.id, name=name, kind="chat")
            await self._enter_chat(session)
            return True

        if low == ":session list":
            if self._selected_book is None:
                self._console.print("[dim]No book selected.[/dim]")
                return False
            sessions = await self._ctx.session_manager.list_by_book(self._selected_book.id)
            self._console.print()
            for s in sessions:
                marker = "→ " if self._current_session and s.session_id == self._current_session.session_id else "  "
                self._console.print(f"{marker}{s.name} [dim]({s.kind}, {s.turn_count} turns)[/dim]")
            return False

        if low == ":usage":
            llm = self._ctx.llm
            if llm is None:
                self._console.print("[dim]LLM not configured.[/dim]")
                return False
            usage = await llm.get_rate_limit_usage()
            rl_mode = self._ctx._config.ratelimit.mode
            if usage is None:
                self._console.print("\n**Rate Limit:** off (no limits configured)\n")
            else:
                mode_label = "requests" if rl_mode == "requests" else "tokens"
                unit = "req" if rl_mode == "requests" else "tok"
                self._console.print(f"\n[bold]Rate Limit:[/bold] {mode_label} mode")
                for wname, info in usage.items():
                    limit = info["limit"]
                    used = info["requests"] if rl_mode == "requests" else info["tokens_used"]
                    if limit == 0:
                        self._console.print(f"  {wname}: {used} {unit} (unlimited)")
                    else:
                        remaining = max(0, limit - used)
                        self._console.print(f"  {wname}: {used:,} / {limit:,} {unit}  ({remaining:,} remaining)")
                self._console.print()
            return False

        if low == ":verbose":
            self._verbose_tools = not self._verbose_tools
            self._console.print(f"[dim]Verbose tools: {'on' if self._verbose_tools else 'off'}[/dim]")
            return False

        if low.startswith(":addindex ") or low.startswith(":addidx "):
            await self._handle_addindex(text)
            return False

        if low.startswith(":rmindex ") or low.startswith(":rmidx "):
            await self._handle_rmindex(text)
            return False

        self._console.print(f"[dim]Unknown command: {text}[/dim]")
        return False

    # ── Phase: compile ─────────────────────────────────────────

    async def _start_compile(self, source_path: str) -> None:
        """Start a compile task."""
        assert self._ctx is not None
        self.phase = "compile"
        self._console.print(f"[dim]Compiling: {source_path}[/dim]")
        try:
            task_id = await self._ctx.compile(source_path)
            self._pending_task_id = task_id
            # Poll until done.
            while True:
                progress = self._ctx.get_task_progress(task_id)
                if progress is None:
                    break
                if progress.status in ("succeeded", "failed"):
                    break
                pct = int(progress.percentage)
                self._console.print(f"[dim]  {progress.stage}: {pct}% ({progress.processed}/{progress.total})[/dim]")
                await asyncio.sleep(1)
            if progress is not None and progress.status == "failed":
                self._console.print(f"[red]Compile failed: {progress.error}[/red]")
            else:
                self._console.print("[dim]Compile complete.[/dim]")
        except Exception as e:
            self._console.print(f"[red]Compile error: {e}[/red]")
        finally:
            await self._refresh_books()
            self.phase = "select"
            await self._phase_select()

    # ── Helpers ────────────────────────────────────────────────

    async def _input(self, prompt: str = "") -> str | None:
        """Read user input via prompt_toolkit."""
        try:
            return await self._prompt.prompt_async(
                HTML(f"<ansicyan>{prompt}</ansicyan> > "),
            )
        except (EOFError, KeyboardInterrupt):
            return None

    async def _refresh_books(self) -> None:
        """Refresh the book list from the store."""
        if self._ctx is None:
            return
        self._books = await self._ctx.list_books()
        self._book_focus_idx = 0

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
            self._console.print(f"[red]Book not found: {book_id}[/red]")
            await self._phase_select()
            return
        await self._enter_session_select(book)

    async def _jump_to_resume(self, session_id: str) -> None:
        """Jump directly to a session (for --resume flag)."""
        assert self._ctx is not None
        session = await self._ctx.session_manager.get(session_id)
        if session is None:
            self._console.print(f"[red]Session not found: {session_id}[/red]")
            await self._refresh_books()
            await self._phase_select()
            return
        # Find book for this session.
        await self._refresh_books()
        book = next((b for b in self._books if b.id == session.book_id), None)
        if book is None:
            self._console.print(f"[red]Book not found for session: {session.book_id}[/red]")
            await self._phase_select()
            return
        self._selected_book = book
        await self._enter_chat(session)

    async def _delete_book(self, book_id: str) -> None:
        """Delete a book."""
        assert self._ctx is not None
        # Remove all indexes first.
        for idx in list(self._selected_book.indexes if self._selected_book else []):
            with contextlib.suppress(Exception):
                await self._ctx.remove_index(book_id, idx)
        self._console.print(f"[dim]Deleted book: {book_id}[/dim]")
        await self._refresh_books()

    async def _handle_addindex(self, text: str) -> None:
        """Handle :addindex command.

        In select phase: :addindex N <type>  (N = book number)
        In chat phase:   :addindex <type>    (uses current book)
        """
        parts = text.split()
        assert self._ctx is not None

        if self.phase == "select":
            # :addindex N <type>
            if len(parts) < 3 or not parts[1].isdigit():
                self._console.print("[dim]Usage: :addindex N <type>[/dim]")
                return
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(self._books)):
                self._console.print(f"[dim]Invalid book number: {idx + 1}[/dim]")
                return
            book = self._books[idx]
            idx_type = parts[2].lower()
        else:
            # :addindex <type> (chat phase)
            if len(parts) < 2:
                self._console.print("[dim]Usage: :addindex <type>[/dim]")
                return
            if self._selected_book is None:
                self._console.print("[dim]No book selected.[/dim]")
                return
            book = self._selected_book
            idx_type = parts[1].lower()

        provider = self._ctx.registry.by_type(idx_type)
        if provider is None:
            self._console.print(f"[dim]Unknown index: {idx_type}[/dim]")
            return
        built = set(book.indexes)
        if idx_type in built:
            self._console.print(f"[dim]{idx_type} already built.[/dim]")
            return
        self._console.print(f"[dim]Building index: {idx_type}...[/dim]")
        try:
            await self._ctx.add_index(book.id, {idx_type})
            self._console.print(f"[dim]Index built: {idx_type}[/dim]")
            await self._refresh_books()
        except Exception as e:
            self._console.print(f"[red]Error: {e}[/red]")

    async def _handle_rmindex(self, text: str) -> None:
        """Handle :rmindex command.

        In select phase: :rmindex N <type>  (N = book number)
        In chat phase:   :rmindex <type>    (uses current book)
        """
        parts = text.split()
        assert self._ctx is not None

        if self.phase == "select":
            # :rmindex N <type>
            if len(parts) < 3 or not parts[1].isdigit():
                self._console.print("[dim]Usage: :rmindex N <type>[/dim]")
                return
            idx = int(parts[1]) - 1
            if not (0 <= idx < len(self._books)):
                self._console.print(f"[dim]Invalid book number: {idx + 1}[/dim]")
                return
            book = self._books[idx]
            idx_type = parts[2].lower()
        else:
            # :rmindex <type> (chat phase)
            if len(parts) < 2:
                self._console.print("[dim]Usage: :rmindex <type>[/dim]")
                return
            if self._selected_book is None:
                self._console.print("[dim]No book selected.[/dim]")
                return
            book = self._selected_book
            idx_type = parts[1].lower()

        built = set(book.indexes)
        if idx_type not in built:
            self._console.print(f"[dim]{idx_type} not built for this book.[/dim]")
            return
        try:
            await self._ctx.remove_index(book.id, idx_type)
            self._console.print(f"[dim]Removed index: {idx_type}[/dim]")
            await self._refresh_books()
        except Exception as e:
            self._console.print(f"[red]Error: {e}[/red]")


__all__ = ["SimpleTui"]
