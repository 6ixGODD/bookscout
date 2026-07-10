"""Toolset wiring for reading over existing indexes."""

from __future__ import annotations

import pathlib
import typing as t

from bookscout.doccompiler.index_provider import IndexContext
from bookscout.tools import BaseTool
from bookscout.tools.toolset import Toolset

from .config import ReadingModeConfig

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore
    from bookscout.embedding import EmbeddingSystem
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger


class ReadingAgentToolset(Toolset):
    """Retrieval tools for reading, built from existing package factories.

    Only tools for indexes the book actually has built are registered.
    Filter set is determined by the IndexManifest table for ``book_id``.
    """

    def __init__(
        self,
        *,
        config: ReadingModeConfig,
        llm: ChatModel,
        embedding: EmbeddingSystem,
        logger: Logger,
        book_id: str,
        registry: t.Any,
        books_store: BooksStore,
        skill_loader: t.Any | None = None,
        external_mcp_configs: t.Sequence[t.Any] | None = None,
    ) -> None:
        super().__init__(
            name="reading_retrieval",
            description="Ontology + index retrieval tools for reading.",
            tools=[],
            logger=logger,
        )
        self.config = config
        self._llm = llm
        self._embedding = embedding
        self._book_id = book_id
        self._registry = registry
        self._books_store = books_store
        self._resources: list[t.Any] = []
        self._skill_loader = skill_loader
        self._external_mcp_configs = list(external_mcp_configs) if external_mcp_configs else []

    async def startup(self) -> None:
        from bookscout.books.tools import create_ontology_tools
        from bookscout.tools.computation import create_computation_tools

        tools: list[BaseTool] = []

        # 1. Ontology tools (always available).
        tools.extend(create_ontology_tools(self._books_store))

        # 2. Computation tools (always available).
        tools.extend(create_computation_tools())

        # 3. Skill fetch tool (only if skill loader is available).
        if self._skill_loader is not None:
            from bookscout.tools.skill_fetch import SkillFetchTool

            tools.append(SkillFetchTool(self._skill_loader))

        # 4. Index-driven tools — only for indexes this book has built.
        built_types = await self._books_store.list_index_types(self._book_id)
        active_providers = [p for p in self._registry.all() if p.index_type in built_types]

        vector_store = None
        if any(p.requires_vector_store for p in active_providers):
            from bookscout.vectorstore.lancedb import LanceDBConfig
            from bookscout.vectorstore.lancedb import LanceDBStore

            vector_store = LanceDBStore(
                LanceDBConfig(
                    uri=self.config.resolved_lancedb_uri,
                    table_name=self.config.lancedb_table_name,
                )
            )
            await vector_store.init()
            self._resources.append(vector_store)

        for provider in active_providers:
            db_path = pathlib.Path(self.config.workspace_root) / "indexes" / f"{provider.db_path_name}.sqlite"
            ctx = IndexContext(
                logger=self.logger,
                books_store=self._books_store,
                llm=self._llm,
                embedding=self._embedding,
                vector_store=vector_store,
                db_path=db_path,
            )
            store = provider.store_factory(ctx)
            if hasattr(store, "startup"):
                await store.startup()
            self._resources.append(store)

            if provider.index_type == "summary":
                tools_list = provider.tool_factory(indexer=None, store=store, ctx=ctx)
                await self._startup_hidden_summary_stores(tools_list)
                tools.extend(tools_list)
            else:
                indexer = provider.indexer_factory(ctx)
                if hasattr(indexer, "startup"):
                    await indexer.startup()
                self._resources.append(indexer)
                tools.extend(provider.tool_factory(indexer=indexer, store=store, ctx=ctx))

        # 5. External MCP tools — connect to configured MCP servers.
        if self._external_mcp_configs:
            from bookscout.tools.mcp_toolset import ExternalMcpToolset

            mcp_toolset = ExternalMcpToolset(
                configs=self._external_mcp_configs,
                logger=self.logger,
            )
            await mcp_toolset.startup()
            self._resources.append(mcp_toolset)
            tools.extend(mcp_toolset.tools)

        self.internal_tools = tools  # pylint: disable=attribute-defined-outside-init
        await super().startup()

    async def shutdown(self) -> None:
        await super().shutdown()
        for resource in reversed(self._resources):
            if hasattr(resource, "shutdown"):
                await resource.shutdown()
            elif hasattr(resource, "close"):
                await resource.close()
        self._resources = []

    async def _startup_hidden_summary_stores(self, tools: t.Sequence[BaseTool]) -> None:
        seen: set[int] = set()
        for tool in tools:
            store = getattr(tool, "_store", None)
            if store is None or id(store) in seen:
                continue
            seen.add(id(store))
            if hasattr(store, "startup"):
                await store.startup()
                self._resources.append(store)
