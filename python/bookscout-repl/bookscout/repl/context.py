"""``ReplContext`` — shared runtime resources for the REPL server and TUI.

Both the stdio :class:`~bookscout.repl.server.ReplServer` and the
:class:`~bookscout.repl.tui.BookScoutTui` need the same set of resources
(BooksStore, LLM, embedding, vector store, parser, builder, TaskManager,
per-book ReadingMode cache). This module extracts that setup so the two
front-ends don't duplicate it.

The context owns no transport — callers consume it directly. The server
wraps it with a :class:`~bookscout.repl.transport.Transport`; the TUI
calls its methods in-process.
"""

from __future__ import annotations

import contextlib
import pathlib
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger
from bookscout.logging.mixin import LoggingMixin

from .config import BookScoutConfig

if t.TYPE_CHECKING:
    from bookscout.agents.context import AgentContext
    from bookscout.agents.mode import StreamChunk
    from bookscout.agents.reading.mode import ReadingMode
    from bookscout.books import Book
    from bookscout.books import BooksStore
    from bookscout.doccompiler import Builder
    from bookscout.doccompiler import EpubParser
    from bookscout.doccompiler import Indexer
    from bookscout.doccompiler import PdfParser
    from bookscout.doccompiler.index_registry import IndexRegistry
    from bookscout.doccompiler.task_manager import TaskManager
    from bookscout.doccompiler.task_manager import TaskProgress
    from bookscout.embedding import EmbeddingSystem
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger
    from bookscout.progress import Monitor
    from bookscout.vectorstore.lancedb import LanceDBStore

    from .session_manager import SessionManager


class ReplContext(LoggingMixin, AsyncResourceMixin):
    """Owns the runtime resources shared by the REPL server and the TUI.

    The context is transport-agnostic. Construct it with a
    :class:`BookScoutConfig`, call :meth:`startup`, then use the
    convenience methods (:meth:`list_books`, :meth:`compile`,
    :meth:`get_task_progress`, :meth:`chat`). Call :meth:`shutdown`
    when done.

    Args:
        config: BookScout configuration (YAML + env + CLI overrides).
        logger: Optional logger; built from ``config.logging`` if omitted.
    """

    def __init__(
        self,
        config: BookScoutConfig,
        *,
        logger: Logger | None = None,
    ) -> None:
        self._config = config
        self._workdir = config.resolved_workdir
        self._data_dir = config.resolved_data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._workdir.mkdir(parents=True, exist_ok=True)
        (self._workdir / "skills").mkdir(parents=True, exist_ok=True)
        (self._workdir / "logs").mkdir(parents=True, exist_ok=True)

        if logger is None:
            logger = _build_logger(config, name="bookscout-repl")
        super().__init__(logger=logger)

        # Resources (populated by startup).
        self._books_store: BooksStore | None = None
        self._llm: ChatModel | None = None
        self._embedding: EmbeddingSystem | None = None
        self._vector_store: LanceDBStore | None = None
        self._task_manager: TaskManager | None = None
        self._epub_parser: EpubParser | None = None
        self._pdf_parser: PdfParser | None = None
        self._builder: Builder | None = None
        self._llm_builder: Builder | None = None
        self._modes: dict[str, ReadingMode] = {}
        self._indexers: list[Indexer] = []
        self._registry: IndexRegistry | None = None
        self._monitor: Monitor | None = None
        self._session_manager: SessionManager | None = None

    async def startup(self) -> None:
        """Initialize all resources from config.

        Missing API keys are tolerated — the corresponding resource is
        left as ``None`` and the offending operation surfaces a clear
        error when invoked.
        """
        from bookscout.books import BooksConfig
        from bookscout.books import BooksStore
        from bookscout.doccompiler import EpubParser
        from bookscout.doccompiler import MineruPdfParser
        from bookscout.doccompiler import RuleBasedBuilder
        from bookscout.doccompiler.task_manager import TaskManager
        from bookscout.embedding.openai import OpenAIEmbedding
        from bookscout.embedding.openai import OpenAIEmbeddingConfig
        from bookscout.llm.config import LLMConfig
        from bookscout.llm.config import OpenAIConfig
        from bookscout.llm.openai import OpenAIChatModel
        from bookscout.vectorstore.lancedb import LanceDBConfig
        from bookscout.vectorstore.lancedb import LanceDBStore

        # BooksStore.
        self._books_store = BooksStore(
            logger=self.logger,
            config=BooksConfig(base_path=self._data_dir, db_name="books.sqlite"),
        )
        await self._books_store.startup()

        # LLM (stateless) — from config, not env.
        cm = self._config.chatmodel
        if cm.api_key:
            self._llm = OpenAIChatModel(
                logger=self.logger,
                config=LLMConfig(
                    backend=OpenAIConfig(
                        api_key=cm.api_key,
                        base_url=cm.base_url,
                        model=cm.model,
                    ),
                    stateless=cm.stateless,
                ),
            )
            await self._llm.startup()

        # Embedding — from config.
        emb = self._config.embedding
        if emb.api_key:
            self._embedding = OpenAIEmbedding(
                OpenAIEmbeddingConfig(
                    api_key=emb.api_key,
                    base_url=emb.base_url,
                    model=emb.model,
                    batch_size=emb.batch_size,
                )
            )

        # Vector store.
        if self._embedding is not None:
            lancedb_dir = self._data_dir / "lancedb"
            lancedb_dir.mkdir(parents=True, exist_ok=True)
            self._vector_store = LanceDBStore(LanceDBConfig(uri=str(lancedb_dir), table_name="bookscout_vectors"))
            await self._vector_store.init()

        # Monitor for fine-grained progress.
        from bookscout.progress import Monitor

        self._monitor = Monitor()

        # Parser + builder.
        self._epub_parser = EpubParser(logger=self.logger)
        self._pdf_parser = MineruPdfParser(logger=self.logger, monitor=self._monitor)
        await self._epub_parser.startup()
        await self._pdf_parser.startup()
        self._builder = RuleBasedBuilder(logger=self.logger)
        await self._builder.startup()

        # Optional LLM tool-driven builder (only when chat is available).
        if self._llm is not None:
            from bookscout.doccompiler import LlmToolBuilder

            self._llm_builder = LlmToolBuilder(logger=self.logger, model=self._llm)
            await self._llm_builder.startup()

        # Indexers — built from registry; only providers whose requirements are met.
        from bookscout.doccompiler.index_provider import IndexContext
        from bookscout.doccompiler.index_registry import IndexRegistry

        self._registry = IndexRegistry.load()
        if self._llm is not None and self._embedding is not None and self._vector_store is not None:
            ctx = IndexContext(
                logger=self.logger,
                books_store=self._books_store,
                llm=self._llm,
                embedding=self._embedding,
                vector_store=self._vector_store,
            )
            for provider in self._registry.all():
                if provider.requires_vector_store and self._vector_store is None:
                    continue
                indexer = provider.indexer_factory(ctx)
                self._indexers.append(indexer)

        # TaskManager.
        self._task_manager = TaskManager(
            logger=self.logger,
            books_store=self._books_store,
            parser=self._epub_parser,
            builder=self._builder,
            llm_model=self._llm,
            indexers=self._indexers if self._indexers else None,
            vector_store=self._vector_store,
            workspace_base=self._data_dir,
            monitor=self._monitor,
        )
        await self._task_manager.startup()

        # SessionManager — global session store.
        from .session_manager import SessionManager

        self._session_manager = SessionManager(workdir=self._workdir, logger=self.logger)
        await self._session_manager.startup()

        # Bootstrap manifest from existing index files (idempotent migration).
        await self._bootstrap_manifest_from_files()

        await super().startup()
        self.logger.info("REPL context started", data_dir=str(self._data_dir))

    async def shutdown(self) -> None:
        """Shut down all resources in reverse order."""
        if self._session_manager is not None:
            await self._session_manager.shutdown()

        for mode in self._modes.values():
            await mode.shutdown()
        self._modes.clear()

        if self._task_manager is not None:
            await self._task_manager.shutdown()
        for indexer in self._indexers:
            with contextlib.suppress(Exception):
                await indexer.shutdown()
        if self._builder is not None:
            await self._builder.shutdown()
        if self._llm_builder is not None:
            with contextlib.suppress(Exception):
                await self._llm_builder.shutdown()
        if self._pdf_parser is not None:
            await self._pdf_parser.shutdown()
        if self._epub_parser is not None:
            await self._epub_parser.shutdown()
        if self._vector_store is not None:
            await self._vector_store.close()
        if self._llm is not None:
            await self._llm.shutdown()
        if self._books_store is not None:
            await self._books_store.shutdown()
        await super().shutdown()

    async def _bootstrap_manifest_from_files(self) -> None:
        """Idempotent backfill: for books whose index sqlite files exist but no
        ``built`` manifest row, insert a ``built`` row with count=0.
        """
        from bookscout.doccompiler.workspace import BookWorkspace

        assert self._books_store is not None
        book_ids = await self._books_store.all_book_ids()
        for book_id in book_ids:
            built = await self._books_store.list_index_types(book_id)
            for provider in self._registry.all():
                if provider.index_type in built:
                    continue
                ws = BookWorkspace.create(self._data_dir, book_id)
                db_path = ws.index_db_path(provider.db_path_name)
                if db_path.exists() and db_path.stat().st_size > 0:
                    await self._books_store.upsert_index(
                        book_id,
                        provider.index_type,
                        "built",
                        count=0,
                    )
                    self.logger.info("manifest bootstrapped", book_id=book_id, index_type=provider.index_type)

    # ── Accessors ─────────────────────────────────────────────
    @property
    def data_dir(self) -> pathlib.Path:
        """Resolved data directory."""
        return self._data_dir

    @property
    def workdir(self) -> pathlib.Path:
        """Resolved workdir root."""
        return self._workdir

    @property
    def books_store(self) -> BooksStore:
        """BooksStore (raises if not started)."""
        if self._books_store is None:
            raise RuntimeError("ReplContext not started")
        return self._books_store

    @property
    def task_manager(self) -> TaskManager:
        """TaskManager (raises if not started)."""
        if self._task_manager is None:
            raise RuntimeError("ReplContext not started")
        return self._task_manager

    @property
    def llm(self) -> ChatModel | None:
        """ChatModel, or ``None`` if no API key was configured."""
        return self._llm

    @property
    def embedding(self) -> EmbeddingSystem | None:
        """Embedding system, or ``None`` if no API key was configured."""
        return self._embedding

    @property
    def has_chat(self) -> bool:
        """Whether chat is available (LLM + embedding both configured)."""
        return self._llm is not None and self._embedding is not None

    @property
    def monitor(self) -> t.Any:
        """The progress Monitor (for fine-grained compile progress)."""
        return self._monitor

    @property
    def registry(self) -> t.Any:
        """The IndexRegistry."""
        return self._registry

    @property
    def session_manager(self) -> SessionManager:
        """The global SessionManager (raises if not started)."""
        if self._session_manager is None:
            raise RuntimeError("ReplContext not started")
        return self._session_manager

    @property
    def has_llm_builder(self) -> bool:
        """Whether the LLM tool-driven builder is available."""
        return self._llm_builder is not None

    @property
    def default_builder(self) -> str:
        """Return the default builder key for new compiles (``"rule"``)."""
        return "rule"

    def select_builder(self, key: str) -> Builder:
        """Pick one of the available builders by ``key``.

        Supported values: ``"rule"`` (always available), ``"llm"``
        (requires LLM configured). Falls back to ``"rule"`` when the
        LLM builder is unavailable so a caller-supplied ``"llm"`` stays
        robust in a no-key dev environment.
        """
        if key == "llm" and self._llm_builder is not None:
            return self._llm_builder  # type: ignore[return-value]
        return self._builder  # type: ignore[return-value]

    # ── Operations ────────────────────────────────────────────
    async def list_books(self) -> list[Book]:
        """List all books in the store."""
        return list(await self.books_store.list_books())

    async def compile(
        self,
        source_path: str,
        *,
        index_types: set[str] | None = None,
        builder: str = "rule",
    ) -> str:
        """Start a compile task. Returns the task id.

        The parser is selected from the source extension (``.pdf`` ->
        MinerU, otherwise EPUB).

        Args:
            source_path: Path to the source file.
            index_types: Optional set of index types to build. See
                :meth:`task_manager.start_compile`.
            builder: Which ontology builder to use (``"rule"`` or ``"llm"``).
                ``"llm"`` falls back to ``"rule"`` when the LLM builder is
                unavailable (no API key).
        """
        ext = pathlib.Path(source_path).suffix.lower()
        parser = self._pdf_parser if ext == ".pdf" else self._epub_parser
        # TaskManager holds a parser slot; swap it for this run.
        self.task_manager._parser = parser  # type: ignore[attr-defined]
        self.task_manager._builder = self.select_builder(builder)  # type: ignore[attr-defined]
        return str(await self.task_manager.start_compile(source_path, index_types=index_types))

    async def build_indexes(
        self,
        book_id: str,
        index_types: list[str] | None = None,
    ) -> str:
        """Start an index-build task. Returns the task id."""
        return str(await self.task_manager.start_index(book_id, index_types))

    async def add_index(self, book_id: str, index_types: set[str]) -> str:
        """Start an incremental index-build task for an existing book.

        Returns the task id.
        """
        return str(await self.task_manager.start_index(book_id, list(index_types)))

    async def remove_index(self, book_id: str, index_type: str) -> None:
        """Remove an index from a book: set manifest 'removed' + delete the sqlite file."""
        from bookscout.doccompiler.workspace import BookWorkspace

        ws = BookWorkspace.create(self._data_dir, book_id)
        provider = self._registry.by_type(index_type)
        db_name = provider.db_path_name if provider else index_type
        db_path = ws.index_db_path(db_name)
        db_path.unlink(missing_ok=True)
        await self.books_store.set_index_status(book_id, index_type, "removed")
        # Invalidate any cached modes for this book (sessions may be keyed by session_id).
        keys_to_pop = [sid for sid, mode in self._modes.items() if mode.config.book_id == book_id]
        for sid in keys_to_pop:
            self._modes.pop(sid, None)

    def get_task_progress(self, task_id: str) -> TaskProgress | None:
        """Poll progress for a running task."""
        return self.task_manager.get_progress(task_id)

    async def get_or_create_mode(self, book_id: str, session_id: str) -> ReadingMode | None:
        """Get (or lazily create) the :class:`ReadingMode` for a session.

        Each session gets its own ``reading_mode_<session_id>.sqlite``,
        isolated from other sessions for the same book.

        Returns ``None`` if the LLM or embedding was not configured.
        """
        if session_id in self._modes:
            return self._modes[session_id]
        if not self.has_chat:
            return None

        from bookscout.agents.reading.agent import READING_SYSTEM_PROMPT
        from bookscout.agents.reading.config import ReadingLLMProfiles
        from bookscout.agents.reading.config import ReadingModeConfig
        from bookscout.agents.reading.mode import ReadingMode

        # Build the skill system.
        from .prompt_builder import PromptBuilder
        from .skill_loader import SkillLoader

        skill_loader = SkillLoader(self._workdir, self._config.skills)
        soul_path = self._workdir / "SOUL.md"
        prompt = PromptBuilder(
            skill_descriptions=skill_loader.list_skills(),
            soul_path=soul_path,
            base_system_prompt=READING_SYSTEM_PROMPT,
        ).build()

        book_dir = self._data_dir / book_id
        book_dir.mkdir(parents=True, exist_ok=True)
        session_db = book_dir / f"reading_mode_{session_id}.sqlite"
        cm = self._config.chatmodel
        config = ReadingModeConfig(
            books_base_path=self._data_dir,
            book_id=book_id,
            db_uri=f"sqlite+aiosqlite:///{session_db}",
            books_db_base_path=self._data_dir,
            lancedb_uri=str(self._data_dir / "lancedb"),
            llm_profiles=ReadingLLMProfiles(
                cheap=cm.model,
                standard=cm.model,
                strong=cm.model,
            ),
        )
        mode = ReadingMode(
            config=config,
            llm=self._llm,  # type: ignore[arg-type]
            embedding=self._embedding,  # type: ignore[arg-type]
            logger=self.logger,
            book_id=book_id,
            registry=self._registry,
            books_store=self._books_store,
            system_prompt=prompt,
            skill_loader=skill_loader,
            external_mcp_configs=self._config.mcp_servers,
        )
        await mode.startup()
        self._modes[session_id] = mode
        return mode

    def make_agent_context(self, book_id: str) -> AgentContext:
        """Build an :class:`AgentContext` for a book chat turn."""
        from bookscout.agents.context import AgentContext

        ctx = AgentContext(llm=self._llm)  # type: ignore[arg-type]
        ctx.extra["book_id"] = book_id
        return ctx

    async def chat(
        self,
        book_id: str,
        session_id: str,
        user_input: str,
    ) -> t.AsyncIterator[StreamChunk]:
        """Stream chat chunks for a user turn.

        Yields :class:`~bookscout.agents.mode.StreamChunk` instances.
        Raises :class:`RuntimeError` if chat is unavailable.
        """
        mode = await self.get_or_create_mode(book_id, session_id)
        if mode is None:
            raise RuntimeError("Cannot create reading mode (missing LLM or embedding)")
        ctx = self.make_agent_context(book_id)
        async for chunk in mode.handle_stream(user_input, ctx=ctx):
            yield chunk


def _build_logger(config: BookScoutConfig, *, name: str) -> Logger:
    """Build a logger from the logging section of a BookScoutConfig."""
    from bookscout.logging.config import TargetConfig

    level_str = config.logging.level.upper()
    targets_cfg: list[TargetConfig] = []

    for tgt in config.logging.targets:
        dest = tgt.dest
        if dest == "file":
            log_file = config.resolve_log_file_path()
            targets_cfg.append(
                TargetConfig(
                    dest=log_file,
                    level=tgt.level.upper(),
                    pretty=tgt.pretty,
                )
            )
        else:
            targets_cfg.append(
                TargetConfig(
                    dest=dest,
                    level=tgt.level.upper(),
                    pretty=tgt.pretty,
                )
            )

    if not targets_cfg:
        targets_cfg.append(TargetConfig(dest="stderr", level=level_str, pretty=True))
        log_file = config.resolve_log_file_path()
        targets_cfg.append(TargetConfig(dest=log_file, level=config.logging.file_level.upper(), pretty=True))

    return build_logger(LoggingConfig(name=name, level=level_str, targets=targets_cfg))


__all__ = ["ReplContext"]
