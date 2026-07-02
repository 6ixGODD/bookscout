"""SharedContext — initializes and holds all shared resources for MCP tools.

On startup, creates and initializes:
  - BooksStore (ontology persistence)
  - ChatModel (stateless, for LLM features)
  - EmbeddingSystem (DashScope-compatible)
  - LanceDBStore (vector storage)
  - Index stores (Summary, Chunk, Graph)
  - Indexers (Summary, Chunk, Graph)
  - TaskManager (async compile/index with progress)

On shutdown, closes everything in reverse order.
"""

from __future__ import annotations

import pathlib
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

from .config import McpServerConfig

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.doccompiler import Builder
    from bookscout.doccompiler import DocParser
    from bookscout.doccompiler.indexer import Indexer
    from bookscout.doccompiler.parser.epub import EpubParser
    from bookscout.doccompiler.parser.pdf import PdfParser
    from bookscout.doccompiler.task_manager import TaskManager
    from bookscout.embedding import EmbeddingSystem
    from bookscout.index.chunk import ChunkIndexer
    from bookscout.index.chunk import ChunkStore
    from bookscout.index.graph import GraphIndexer
    from bookscout.index.graph import GraphStore
    from bookscout.index.summary import SummaryIndexer
    from bookscout.index.summary import SummaryStore
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger
    from bookscout.vectorstore.lancedb import LanceDBStore


class SharedContext(LoggingMixin, AsyncResourceMixin):
    """Holds all shared resources for the MCP server.

    Args:
        logger: Logger instance.
        config: MCP server configuration.
    """

    def __init__(self, logger: Logger, config: McpServerConfig) -> None:
        super().__init__(logger=logger)
        self._epub_parser: EpubParser | None = None  # type
        self._pdf_parser: PdfParser | None = None  # type
        self._config = config
        self.data_dir = pathlib.Path(config.data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Resources (initialized in startup).
        self.books_store: BooksStore | None = None
        self.llm_model: ChatModel | None = None
        self.embedding: EmbeddingSystem | None = None
        self.vector_store: LanceDBStore | None = None
        self.parser: DocParser | None = None
        self.builder: Builder | None = None
        self.indexers: list[Indexer] = []
        self.task_manager: TaskManager | None = None

        # Index stores (for retrieval tools).
        self.summary_store: SummaryStore | None = None
        self.chunk_store: ChunkStore | None = None
        self.graph_store: GraphStore | None = None
        # Indexers for retrieval (have embedding/vector_store bound).
        self.chunk_indexer: ChunkIndexer | None = None
        self.graph_indexer: GraphIndexer | None = None
        self.summary_indexer: SummaryIndexer | None = None

    async def startup(self) -> None:
        """Initialize all shared resources."""
        self.logger.info("shared context starting", data_dir=str(self.data_dir))

        # BooksStore.
        from bookscout.books import BooksConfig
        from bookscout.books import BooksStore

        self.books_store = BooksStore(
            logger=self.logger,
            config=BooksConfig(base_path=self.data_dir, db_name="books.sqlite"),
        )
        await self.books_store.startup()

        # ChatModel (stateless).
        if self._config.llm.api_key:
            from bookscout.llm.config import LLMConfig
            from bookscout.llm.config import OpenAIConfig
            from bookscout.llm.openai import OpenAIChatModel

            self.llm_model = OpenAIChatModel(
                logger=self.logger,
                config=LLMConfig(
                    backend=OpenAIConfig(
                        api_key=self._config.llm.api_key,
                        base_url=self._config.llm.base_url,
                        model=self._config.llm.model,
                    ),
                    stateless=True,
                ),
            )
            await self.llm_model.startup()

        # Embedding.
        if self._config.embedding.api_key:
            from bookscout.embedding.openai import OpenAIEmbedding
            from bookscout.embedding.openai import OpenAIEmbeddingConfig

            self.embedding = OpenAIEmbedding(
                OpenAIEmbeddingConfig(
                    api_key=self._config.embedding.api_key,
                    base_url=self._config.embedding.base_url,
                    model=self._config.embedding.model,
                    batch_size=self._config.embedding.batch_size,
                )
            )

        # Vector store.
        if self.embedding is not None:
            from bookscout.vectorstore.lancedb import LanceDBConfig
            from bookscout.vectorstore.lancedb import LanceDBStore

            lancedb_dir = self.data_dir / "lancedb"
            lancedb_dir.mkdir(parents=True, exist_ok=True)
            self.vector_store = LanceDBStore(
                LanceDBConfig(
                    uri=str(lancedb_dir),
                    table_name="bookscout_vectors",
                )
            )
            await self.vector_store.init()

        # Parser (MinerU for PDF, local for EPUB).
        from bookscout.doccompiler import EpubParser
        from bookscout.doccompiler import MineruPdfParser

        # We need to decide parser per-source-file at compile time.
        # For the MCP server, we store both parsers and select at compile time.
        # The TaskManager will use the appropriate one based on file extension.
        # For simplicity, we default to EpubParser here; the compile tool
        # can switch to MineruPdfParser for PDFs.
        self._epub_parser = EpubParser(logger=self.logger)
        self._pdf_parser = MineruPdfParser(logger=self.logger)

        # Builder (rule-based by default).
        from bookscout.doccompiler import RuleBasedBuilder

        self.builder = RuleBasedBuilder(logger=self.logger)
        await self.builder.startup()

        # Indexers.
        estimate_fn = None
        if self.llm_model is not None:
            from bookscout.llm import ChatModel

            estimate_fn = ChatModel.estimate_token

        if self.llm_model is not None:
            from bookscout.index.summary import SummaryIndexer

            self.summary_indexer = SummaryIndexer(
                logger=self.logger,
                books_store=self.books_store,
                model=self.llm_model,
            )
            self.indexers.append(self.summary_indexer)

        if self.embedding is not None and self.vector_store is not None:
            from bookscout.index.chunk import ChunkIndexer

            self.chunk_indexer = ChunkIndexer(
                logger=self.logger,
                books_store=self.books_store,
                embedding=self.embedding,
                vector_store=self.vector_store,
                estimate_token_fn=estimate_fn,
            )
            self.indexers.append(self.chunk_indexer)

        if self.llm_model is not None and self.embedding is not None and self.vector_store is not None:
            from bookscout.index.graph import GraphIndexer

            self.graph_indexer = GraphIndexer(
                logger=self.logger,
                books_store=self.books_store,
                model=self.llm_model,
                embedding=self.embedding,
                vector_store=self.vector_store,
                estimate_token_fn=estimate_fn,
            )
            self.indexers.append(self.graph_indexer)

        # Start indexers.
        for indexer in self.indexers:
            await indexer.startup()

        # TaskManager.
        # The TaskManager needs a parser. Since we support both EPUB and PDF,
        # we default to epub_parser; the compile tool will handle switching.
        # Actually, TaskManager creates a Compiler internally with the given parser.
        # For PDF support, we need the MinerU token in the environment.
        import os

        from bookscout.doccompiler.task_manager import TaskManager

        if self._config.mineru.api_token:
            os.environ.setdefault("MINERU_API_TOKEN", self._config.mineru.api_token)

        self.task_manager = TaskManager(
            logger=self.logger,
            books_store=self.books_store,
            parser=self._epub_parser,  # Default; compile tool can override.
            builder=self.builder,
            llm_model=self.llm_model,
            indexers=self.indexers,
            vector_store=self.vector_store,
            workspace_base=self.data_dir,
        )
        await self.task_manager.startup()

        await super().startup()
        self.logger.info("shared context started")

    async def shutdown(self) -> None:
        """Shut down all resources in reverse order."""
        if self.task_manager is not None:
            await self.task_manager.shutdown()
        for indexer in reversed(self.indexers):
            await indexer.shutdown()
        if self.builder is not None:
            await self.builder.shutdown()
        if self.vector_store is not None:
            await self.vector_store.close()
        if self.llm_model is not None:
            await self.llm_model.shutdown()
        if self.books_store is not None:
            await self.books_store.shutdown()
        await super().shutdown()
        self.logger.info("shared context stopped")

    def get_book_workspace(self, book_id: str) -> pathlib.Path:
        """Get the workspace directory for a book.

        Args:
            book_id: The book ID.

        Returns:
            Path to the book's workspace directory.
        """
        return self.data_dir / book_id


__all__ = ["SharedContext"]
