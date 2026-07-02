"""Compiler abstraction with metrics and progress tracking (spec §12, §16.7).

The :class:`Compiler` orchestrates the full compilation pipeline:
parse → build ontology → validate → persist → build indexes.

Components are injected:
    * ``parser`` — a :class:`DocParser` for source document parsing.
    * ``builder`` — a :class:`Builder` for BookNode tree construction.
    * ``indexers`` — a list of :class:`Indexer` instances for derived indexes.
    * ``llm_model`` — optional ChatModel for LLM metadata extraction.

The compiler tracks :class:`CompileMetrics` throughout.
"""

from __future__ import annotations

import dataclasses
import enum
import pathlib
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

from .builder import Builder
from .builder.metadata import LlmMetadataExtractor
from .indexer import Indexer
from .types import ParserResult
from .workspace import BookWorkspace

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler import DocParser
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger


class CompileStatus(enum.StrEnum):
    """Compilation status (spec §12.1)."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CompileStage(enum.StrEnum):
    """Compilation stage (spec §12.2)."""

    LOAD_SOURCE = "load_source"
    PARSE_SOURCE = "parse_source"
    GENERATE_CONTENT = "generate_content"
    BUILD_ONTOLOGY = "build_ontology"
    VALIDATE_ONTOLOGY = "validate_ontology"
    PERSIST_ONTOLOGY = "persist_ontology"
    BUILD_INDEXES = "build_indexes"
    FINISHED = "finished"


@dataclasses.dataclass(slots=True)
class CompileMetrics:
    """Compilation metrics (spec §12.3)."""

    status: str = CompileStatus.PENDING.value
    stage: str = CompileStage.LOAD_SOURCE.value
    total_chars: int = 0
    processed_chars: int = 0
    total_chunks: int = 0
    processed_chunks: int = 0
    node_count: int = 0
    completed_node_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    rollback_count: int = 0
    started_at: str = ""
    updated_at: str = ""
    finished_at: str = ""
    batch_count: int = 0
    current_batch: int = 0
    book_id: str = ""
    content_path: str = ""
    mapping_db_path: str = ""
    report_path: str = ""


@dataclasses.dataclass(slots=True)
class CompileResult:
    """Result of a compilation run."""

    book: t.Any
    nodes: list[t.Any]
    parser_result: ParserResult
    workspace: BookWorkspace
    metrics: CompileMetrics


class Compiler(LoggingMixin, AsyncResourceMixin):
    """Document compiler — orchestrates parse → build → validate → persist → index.

    Args:
        logger: Logger instance.
        parser: The document parser to use.
        books_store: The books store for ontology persistence.
        builder: The ontology builder (rule-based or LLM tool-driven).
        llm_model: Optional ChatModel for LLM metadata extraction.
        indexers: Optional list of Indexer instances for derived indexes.
        workspace_base: Base directory for book workspaces.
    """

    def __init__(
        self,
        logger: Logger,
        parser: DocParser,
        books_store: BooksStore,
        builder: Builder,
        llm_model: ChatModel | None = None,
        indexers: t.Sequence[Indexer] | None = None,
        workspace_base: pathlib.Path | str = "output",
    ) -> None:
        super().__init__(logger=logger)
        self._parser = parser
        self._books_store = books_store
        self._builder = builder
        self._llm_model = llm_model
        self._indexers = list(indexers) if indexers else []
        self._workspace_base = pathlib.Path(workspace_base).resolve()
        self._metrics = CompileMetrics()

    @property
    def metrics(self) -> CompileMetrics:
        """Current compilation metrics."""
        return self._metrics

    def _update(self, **kwargs: t.Any) -> None:
        """Update metrics fields and the updated_at timestamp."""
        from bookscout.core.lib.utils import utcnow

        for k, v in kwargs.items():
            setattr(self._metrics, k, v)
        self._metrics.updated_at = utcnow().isoformat()

    async def startup(self) -> None:
        """Start parser, books store, builder, LLM model, and indexers."""
        await self._parser.startup()
        await self._books_store.startup()
        await self._builder.startup()
        if self._llm_model is not None:
            await self._llm_model.startup()
        for indexer in self._indexers:
            await indexer.startup()
        await super().startup()
        self.logger.info("compiler started", builder=type(self._builder).__name__)

    async def shutdown(self) -> None:
        """Shut down all components in reverse order."""
        for indexer in self._indexers:
            await indexer.shutdown()
        if self._llm_model is not None:
            await self._llm_model.shutdown()
        await self._builder.shutdown()
        await self._books_store.shutdown()
        await self._parser.shutdown()
        await super().shutdown()

    async def compile(self, source_path: pathlib.Path, book_id: str | None = None) -> CompileResult:
        """Compile a source document into a persisted ontology + indexes.

        Args:
            source_path: Path to the source file (EPUB, PDF, etc.).
            book_id: Optional book id; auto-generated when ``None``.

        Returns:
            A :class:`CompileResult`.
        """
        import aiofiles

        from bookscout.books import Book
        from bookscout.core.lib.utils import gen_id
        from bookscout.core.lib.utils import utcnow

        if book_id is None:
            book_id = gen_id(prefix="book_")

        self._metrics = CompileMetrics(
            status=CompileStatus.RUNNING.value,
            started_at=utcnow().isoformat(),
            book_id=book_id,
        )

        try:
            # Stage 1: load_source
            self._update(stage=CompileStage.LOAD_SOURCE.value)
            self.logger.info("stage: load_source", source=str(source_path))
            workspace = BookWorkspace.create(self._workspace_base, book_id)

            # Stage 2: parse_source
            self._update(stage=CompileStage.PARSE_SOURCE.value)
            self.logger.info("stage: parse_source")
            parser_result = await self._parser.parse(source_path, book_id, workspace)

            self._update(
                stage=CompileStage.GENERATE_CONTENT.value,
                content_path=parser_result.content_path,
                mapping_db_path=parser_result.mapping_db_path,
            )
            self.logger.info("stage: generate_content", path=parser_result.content_path)

            # Read CONTENT.md
            async with aiofiles.open(parser_result.content_path, encoding="utf-8") as f:
                content = t.cast(str, await f.read())
            self._update(total_chars=len(content))

            # Stage 3: build_ontology
            self._update(stage=CompileStage.BUILD_ONTOLOGY.value)
            self.logger.info("stage: build_ontology", builder=type(self._builder).__name__)

            # LLM metadata extraction (if model available).
            metadata_dict: dict[str, t.Any] = dict(parser_result.metadata)
            if self._llm_model is not None:
                self.logger.info("extracting metadata via LLM")
                extractor = LlmMetadataExtractor(logger=self.logger, model=self._llm_model)
                extracted = await extractor.extract(content)
                metadata_dict = {
                    "title": extracted.title or metadata_dict.get("title", ""),
                    "author": extracted.author or metadata_dict.get("author", ""),
                    "isbn": extracted.isbn or metadata_dict.get("isbn", ""),
                    "publisher": extracted.publisher or metadata_dict.get("publisher", ""),
                    "language": extracted.language or metadata_dict.get("language", ""),
                    "extras": {**metadata_dict.get("extras", {}), **extracted.extras},
                }

            # Build BookNode tree via the injected builder.
            build_result = await self._builder.build(
                book_id,
                content,
                book_title=metadata_dict.get("title", ""),
            )
            nodes = build_result.nodes

            # Merge builder-provided metadata.
            for key in ("title", "author", "isbn", "publisher", "language"):
                val = build_result.metadata.get(key, "")
                if val:
                    metadata_dict[key] = val
            if build_result.metadata.get("extras"):
                metadata_dict["extras"] = {
                    **metadata_dict.get("extras", {}),
                    **build_result.metadata["extras"],
                }

            self._update(
                node_count=len(nodes),
                completed_node_count=len(nodes),
                rollback_count=build_result.rollback_count,
            )

            # Build Book object
            book = Book.new(
                book_id=book_id,
                title=metadata_dict.get("title", ""),
                author=metadata_dict.get("author", ""),
                isbn=metadata_dict.get("isbn", ""),
                publisher=metadata_dict.get("publisher", ""),
                language=metadata_dict.get("language", ""),
                extras=metadata_dict.get("extras", {}),
                content_path=parser_result.content_path,
                source_path=parser_result.source_info.file_path,
                checksum=parser_result.source_info.checksum,
            )

            # Stage 4: validate_ontology
            self._update(stage=CompileStage.VALIDATE_ONTOLOGY.value)
            self.logger.info("stage: validate_ontology")
            # Tree validation happens inside create_nodes.

            # Stage 5: persist_ontology
            self._update(stage=CompileStage.PERSIST_ONTOLOGY.value)
            self.logger.info("stage: persist_ontology")
            await self._books_store.create_book(book)
            await self._books_store.create_nodes(book_id, nodes)

            # Stage 6: build_indexes
            if self._indexers:
                self._update(stage=CompileStage.BUILD_INDEXES.value)
                self.logger.info("stage: build_indexes", count=len(self._indexers))
                for indexer in self._indexers:
                    try:
                        self.logger.info("building index", type=indexer.index_type)
                        result = await indexer.build_index(book_id, workspace)
                        self.logger.info(
                            "index built",
                            type=result.index_type,
                            count=result.count,
                        )
                    except Exception as e:  # pylint: disable=broad-exception-caught
                        self.logger.warning(
                            "index build failed",
                            type=indexer.index_type,
                            error=str(e),
                        )

            # Stage 7: finished
            self._update(
                stage=CompileStage.FINISHED.value,
                status=CompileStatus.SUCCEEDED.value,
                finished_at=utcnow().isoformat(),
            )
            self.logger.info("compilation succeeded", book_id=book_id, nodes=len(nodes))

            return CompileResult(
                book=book,
                nodes=nodes,
                parser_result=parser_result,
                workspace=workspace,
                metrics=self._metrics,
            )

        except Exception as e:  # pylint: disable=broad-exception-caught
            self._update(
                status=CompileStatus.FAILED.value,
                error_count=self._metrics.error_count + 1,
                finished_at=utcnow().isoformat(),
            )
            self.logger.exception("compilation failed", book_id=book_id, error=str(e))
            raise
