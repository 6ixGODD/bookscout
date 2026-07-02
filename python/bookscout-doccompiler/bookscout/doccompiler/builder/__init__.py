"""Builder abstraction — constructs a BookNode tree from parsed content.

Every builder implements :class:`Builder`, taking book_id + content and
producing a list of :class:`bookscout.books.BookNode` objects forming a
valid tree.

Two implementations:
    * :class:`bookscout.doccompiler.builder.rule.RuleBasedBuilder` — fast,
      rule-based heading parsing (spec §7).
    * :class:`bookscout.doccompiler.builder.llm_tool.LlmToolBuilder` — LLM
      tool-driven construction (spec §8, §9).
"""

from __future__ import annotations

import abc
import dataclasses
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.books import BookNode
    from bookscout.logging import Logger


@dataclasses.dataclass(slots=True)
class BuildResult:
    """Result of an ontology build operation.

    Attributes:
        nodes: The built BookNode tree (root first, then descendants).
        metadata: Extracted metadata (title, author, etc.).
        rollback_count: Number of rollbacks (LLM tool mode; 0 for rule mode).
    """

    nodes: list[BookNode]
    metadata: dict[str, t.Any]
    rollback_count: int


class Builder(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract base class for ontology builders.

    Subclasses implement :meth:`build` to produce a BookNode tree.

    Args:
        logger: Logger instance.
    """

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger=logger)

    @abc.abstractmethod
    async def build(
        self,
        book_id: str,
        content: str,
        book_title: str = "",
    ) -> BuildResult:
        """Build a BookNode tree from content.

        Args:
            book_id: The book id.
            content: The full CONTENT.md text.
            book_title: The book title (for root node title).

        Returns:
            A :class:`BuildResult` with the built tree and metadata.
        """
