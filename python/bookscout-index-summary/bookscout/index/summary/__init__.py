"""Summary Index — tree-aggregated LLM summaries with token-budget awareness (spec §11.1).

Builds summaries bottom-up: leaf nodes first, then parents aggregate
their own content + children summaries, up to the root (book summary).

For each node, the content (+ child summaries) is checked against the
LLM token budget via :meth:`ChatModel.estimate_token`. If it exceeds
the budget, the content is split into sub-fragments, each summarized
individually, then those sub-summaries are summarized again to produce
the node's final summary (summary-of-summaries).
"""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import typing as t

from sqlmodel import select

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.doccompiler.indexer import IndexProgress as IndexProgress
from bookscout.doccompiler.indexer import IndexResult
from bookscout.doccompiler.indexer import Indexer
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .models import SummaryModel

if t.TYPE_CHECKING:
    from bookscout.books import BookNode
    from bookscout.books import BooksStore
    from bookscout.doccompiler.workspace import BookWorkspace
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger

# Default token budgets.
# Summary input budget: how many tokens of content we feed to the LLM per call.
DEFAULT_SUMMARY_INPUT_BUDGET = 3000
# Summary output budget: max tokens for the generated summary.
DEFAULT_SUMMARY_OUTPUT_MAX_TOKENS = 2048
# When splitting content for summary-of-summaries, leave room for the prompt overhead.
_PROMPT_OVERHEAD_TOKENS = 200


_SUMMARY_SYSTEM_PROMPT = """\
You are a book content summarizer. Given a section of a book, produce a concise summary (2-5 sentences) that captures the key points. If child section summaries are provided, incorporate them into a higher-level summary. Return ONLY the summary text, no headers or formatting."""


@dataclasses.dataclass(frozen=True, slots=True)
class SummaryEntry:
    """A summary record associated with a BookNode.

    Attributes:
        book_id: Owning book id.
        node_id: The node this summary belongs to.
        node_title: The node's title.
        level: The node's tree level.
        summary_text: The summary text.
    """

    book_id: str
    node_id: str
    node_title: str
    level: int
    summary_text: str


class SummaryStore(LoggingMixin, AsyncResourceMixin):
    """SQLite-backed store for node summaries (spec §11.1).

    Args:
        logger: Logger instance.
        db_path: Path to the summary SQLite database.
    """

    def __init__(self, logger: Logger, db_path: pathlib.Path) -> None:
        super().__init__(logger=logger)
        self._db_path = db_path
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{db_path.as_posix()}"),
            logger=logger,
        )

    async def startup(self) -> None:
        await self._sqlite.startup()
        await self._sqlite.create_all([SummaryModel])
        await super().startup()

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()

    async def upsert_summary(
        self,
        book_id: str,
        node_id: str,
        node_title: str,
        level: int,
        summary_text: str,
    ) -> None:
        """Insert or update a node summary."""
        async with self._sqlite.session() as session:
            stmt = select(SummaryModel).where(
                SummaryModel.book_id == book_id,
                SummaryModel.node_id == node_id,
            )
            existing = (await session.execute(stmt)).scalars().first()
            if existing is not None:
                existing.node_title = node_title
                existing.level = level
                existing.summary_text = summary_text
            else:
                session.add(
                    SummaryModel(
                        book_id=book_id,
                        node_id=node_id,
                        node_title=node_title,
                        level=level,
                        summary_text=summary_text,
                    )
                )
            await session.commit()

    async def get_summary(self, book_id: str, node_id: str) -> SummaryEntry | None:
        """Get a single node summary."""
        async with self._sqlite.session() as session:
            stmt = select(SummaryModel).where(
                SummaryModel.book_id == book_id,
                SummaryModel.node_id == node_id,
            )
            row = (await session.execute(stmt)).scalars().first()
            if row is None:
                return None
            return SummaryEntry(
                book_id=row.book_id,
                node_id=row.node_id,
                node_title=row.node_title,
                level=row.level,
                summary_text=row.summary_text,
            )

    async def list_summaries(
        self,
        book_id: str,
        *,
        node_ids: list[str] | None = None,
    ) -> list[SummaryEntry]:
        """List summaries for a book, optionally filtered by node ids."""
        async with self._sqlite.session() as session:
            stmt = select(SummaryModel).where(SummaryModel.book_id == book_id)
            if node_ids:
                # WHERE book_id = ? AND node_id IN (?, ?, ...)
                stmt = stmt.where(SummaryModel.node_id.in_(node_ids))
            rows = (await session.execute(stmt)).scalars().all()
            return [
                SummaryEntry(
                    book_id=r.book_id,
                    node_id=r.node_id,
                    node_title=r.node_title,
                    level=r.level,
                    summary_text=r.summary_text,
                )
                for r in rows
            ]

    async def delete_all(self, book_id: str) -> None:
        """Delete all summaries for a book."""
        async with self._sqlite.session() as session:
            stmt = select(SummaryModel).where(SummaryModel.book_id == book_id)
            for row in (await session.execute(stmt)).scalars().all():
                await session.delete(row)
            await session.commit()


class SummaryIndexer(Indexer):
    """Builds a Summary Index using LLM tree aggregation (spec §11.1).

    For each node, content is checked against the token budget. If it
    exceeds the budget, the content is split into sub-fragments, each
    summarized individually, then those sub-summaries are summarized
    again to produce the node's final summary.

    Args:
        logger: Logger instance.
        books_store: The BooksStore to read node content from.
        model: A started ChatModel for LLM summaries.
        input_budget: Max tokens of content to feed the LLM per call.
        output_max_tokens: Max tokens for generated summaries.
    """

    def __init__(
        self,
        logger: Logger,
        books_store: BooksStore,
        model: ChatModel,
        input_budget: int = DEFAULT_SUMMARY_INPUT_BUDGET,
        output_max_tokens: int = DEFAULT_SUMMARY_OUTPUT_MAX_TOKENS,
    ) -> None:
        super().__init__(logger=logger, books_store=books_store)
        self._model = model
        self._input_budget = input_budget
        self._output_max_tokens = output_max_tokens

    @property
    def index_type(self) -> str:
        return "summary"

    async def build_index(
        self,
        book_id: str,
        workspace: BookWorkspace,
        *,
        monitor: t.Any = None,
        parent_id: str | None = None,
    ) -> IndexResult:
        """Build summaries for all nodes in a book's tree.

        Processes nodes level-by-level from deepest to root.  All nodes at
        the same level are independent (each depends only on its children,
        which live at deeper levels and have already been summarised) and
        are run concurrently via ``asyncio.gather``.
        """
        mtid = monitor.start("summarize", total=0, parent_id=parent_id) if monitor else None

        db_path = workspace.index_db_path("summary")
        store = SummaryStore(logger=self.logger, db_path=db_path)
        await store.startup()
        try:
            tree = await self._books_store.get_tree(book_id)
            self.logger.info("summary build starting", book_id=book_id, nodes=len(tree))
            self._update_progress(total=len(tree), processed=0, status="running", error="")
            if monitor and mtid:
                monitor.set_total(mtid, len(tree))

            if not tree:
                self._update_progress(status="done")
                return IndexResult(index_type="summary", count=0, progress=self.progress)

            children_map: dict[str, list[BookNode]] = {}
            nodes_by_level: dict[int, list[BookNode]] = {}
            for node in tree:
                children_map.setdefault(node.parent_id, []).append(node)
                nodes_by_level.setdefault(node.level, []).append(node)

            count = 0
            _count_lock = asyncio.Lock()

            async def _summarize_node(node: BookNode) -> int:
                nonlocal count
                children = children_map.get(node.id, [])

                own_content = await self._books_store.read_node_content(node.id)
                child_summaries: list[str] = []
                for child in children:
                    child_entry = await store.get_summary(book_id, child.id)
                    if child_entry and child_entry.summary_text:
                        child_summaries.append(f"[{child.title}] {child_entry.summary_text}")

                if child_summaries:
                    full_text = (
                        f"Section: {node.title or '(untitled)'}\n\n"
                        f"Own content:\n{own_content}\n\n"
                        f"Child section summaries:\n" + "\n".join(child_summaries) + "\n\n"
                        "Produce a summary that covers this section and its subsections."
                    )
                elif own_content:
                    full_text = (
                        f"Section: {node.title or '(untitled)'}\n\n"
                        f"Content:\n{own_content}\n\n"
                        f"Produce a concise summary of this section."
                    )
                else:
                    return 0

                summary_text = await self._summarize_with_budget(full_text)
                if not summary_text:
                    return 0

                await store.upsert_summary(
                    book_id=book_id,
                    node_id=node.id,
                    node_title=node.title,
                    level=node.level,
                    summary_text=summary_text,
                )
                async with _count_lock:
                    count += 1
                    self._update_progress(processed=count)
                if monitor and mtid:
                    monitor.advance(mtid, 1)
                self.logger.debug(
                    "summary generated",
                    node_id=node.id,
                    title=node.title,
                    level=node.level,
                    summary_len=len(summary_text),
                )
                return 1

            # Process deepest levels first — children before parents.
            for level in sorted(nodes_by_level.keys(), reverse=True):
                batch = nodes_by_level[level]
                self.logger.debug(
                    "summarizing level",
                    level=level,
                    count=len(batch),
                )
                await asyncio.gather(*(_summarize_node(n) for n in batch))

            self._update_progress(status="done")
            if monitor and mtid:
                monitor.finish(mtid)
            self.logger.info("summary build finished", book_id=book_id, count=count)
            return IndexResult(index_type="summary", count=count, progress=self.progress)
        finally:
            await store.shutdown()

    async def _summarize_with_budget(self, text: str) -> str:
        """Summarize text, splitting if it exceeds the token budget.

        If the text fits within the budget, summarize it directly.
        If it exceeds the budget, split into sub-fragments, summarize
        each concurrently, then summarize the sub-summaries.
        """

        token_count = self._model.estimate_token(text)
        effective_budget = self._input_budget - _PROMPT_OVERHEAD_TOKENS

        if token_count <= effective_budget:
            return await self._llm_summarize(text)

        # Need to split. Calculate how many fragments.
        num_fragments = (token_count + effective_budget - 1) // effective_budget
        self.logger.debug(
            "content exceeds budget, splitting",
            tokens=token_count,
            fragments=num_fragments,
            budget=effective_budget,
        )

        fragments = self._split_text(text, num_fragments, effective_budget)

        # Summarize all fragments concurrently.
        sub_summaries: list[str] = [s for s in await asyncio.gather(*(self._llm_summarize(f) for f in fragments)) if s]

        if not sub_summaries:
            return ""
        if len(sub_summaries) == 1:
            return sub_summaries[0]

        combined = "\n\n".join(sub_summaries)
        combined_prompt = (
            "The following are summaries of different parts of the same section. "
            "Produce a single coherent summary that combines them:\n\n" + combined
        )
        return await self._llm_summarize(combined_prompt)

    async def _llm_summarize(self, text: str) -> str:
        """Call the LLM to summarize a single text fragment.

        Args:
            text: The text to summarize.

        Returns:
            The summary text, or empty string on failure.
        """
        from bookscout.llm.types import CompletionOptions
        from bookscout.llm.types import SystemMessage
        from bookscout.llm.types import UserMessage

        try:
            response = await self._model.chat_completion(
                [
                    SystemMessage(content=_SUMMARY_SYSTEM_PROMPT),
                    UserMessage(content=text),
                ],
                options=CompletionOptions(max_tokens=self._output_max_tokens, temperature=0.0),
            )
            return response["message"].content.strip()  # type: ignore[no-any-return]
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.warning("LLM summary call failed", error=str(e))
            return ""

    @staticmethod
    def _split_text(text: str, num_fragments: int, budget_tokens: int) -> list[str]:
        """Split text into fragments at paragraph boundaries.

        Tries to split on double-newlines (paragraph breaks) to keep
        fragments coherent.

        Args:
            text: The text to split.
            num_fragments: Target number of fragments.
            budget_tokens: Token budget per fragment.

        Returns:
            List of text fragments.
        """
        if num_fragments <= 1:
            return [text]

        # Split on paragraph boundaries.
        paragraphs = text.split("\n\n")
        fragments: list[str] = []
        current: list[str] = []
        current_tokens = 0

        for para in paragraphs:
            para_tokens = max(1, len(para) // 4)  # Quick estimate.
            if current_tokens + para_tokens > budget_tokens and current:
                fragments.append("\n\n".join(current))
                current = [para]
                current_tokens = para_tokens
            else:
                current.append(para)
                current_tokens += para_tokens

        if current:
            fragments.append("\n\n".join(current))

        return fragments if fragments else [text]


__all__ = [
    "SummaryEntry",
    "SummaryIndexer",
    "SummaryStore",
]
