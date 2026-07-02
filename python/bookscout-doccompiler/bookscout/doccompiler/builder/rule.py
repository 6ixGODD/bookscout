"""Rule-based ontology builder (spec §7, §16.5).

Builds a ``BookNode`` tree from a ``CONTENT.md`` file by parsing Markdown
headings, normalizing title hierarchy (§7.3), and computing
``title_offset``/``title_length``/``content_offset``/``content_length`` for
each node.

This is the first-phase builder: fast, token-free, and best suited for
documents with relatively clear heading structure.
"""

from __future__ import annotations

import dataclasses
import re
import typing as t

from bookscout.books import BookNode

from . import BuildResult
from . import Builder

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")


@dataclasses.dataclass(slots=True)
class _RawHeading:
    """A raw heading parsed from CONTENT.md before normalization."""

    line_offset: int
    line_length: int
    raw_level: int
    title: str
    line_end_offset: int


class RuleBasedBuilder(Builder):
    """Builds a ``BookNode`` tree from ``CONTENT.md`` headings.

    Implements spec §7.3 (title normalization) and §7.4 (node construction).
    """

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger=logger)

    async def build(
        self,
        book_id: str,
        content: str,
        book_title: str = "",
    ) -> BuildResult:
        """Build a BookNode tree from markdown content.

        Args:
            book_id: The owning book id.
            content: The full ``CONTENT.md`` text.
            book_title: The book title; used as the root node's title.

        Returns:
            A :class:`BuildResult` with the built tree.
        """
        nodes = self.build_nodes(book_id, content, book_title)
        return BuildResult(nodes=nodes, metadata={"title": book_title}, rollback_count=0)

    def build_nodes(self, book_id: str, content: str, book_title: str = "") -> list[BookNode]:
        """Build a complete ``BookNode`` tree from markdown content.

        Args:
            book_id: The owning book id.
            content: The full ``CONTENT.md`` text.
            book_title: The book title; used as the root node's title
                (spec §3.3 #11). Defaults to ``""``.

        Returns:
            A list of ``BookNode`` objects forming a valid tree (root first,
            then descendants in document order).
        """
        raw_headings = self._parse_headings(content)
        self.logger.debug("headings parsed", count=len(raw_headings))

        normalized = self._normalize_levels(raw_headings)
        self.logger.debug("levels normalized", count=len(normalized))

        nodes = self._build_tree(book_id, content, normalized, book_title)
        self.logger.info("tree built", book_id=book_id, node_count=len(nodes))
        return nodes

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _parse_headings(content: str) -> list[_RawHeading]:
        """Parse all markdown headings from content.

        Args:
            content: The full markdown text.

        Returns:
            A list of :class:`_RawHeading` in document order.
        """
        headings: list[_RawHeading] = []
        offset = 0
        for line in content.split("\n"):
            m = _HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()
                line_len = len(line)
                headings.append(
                    _RawHeading(
                        line_offset=offset,
                        line_length=line_len,
                        raw_level=level,
                        title=title,
                        line_end_offset=offset + line_len + 1,  # +1 for \n
                    )
                )
            offset += len(line) + 1
        return headings

    # ------------------------------------------------------------------ normalization (§7.3)

    @staticmethod
    def _normalize_levels(headings: list[_RawHeading]) -> list[tuple[_RawHeading, int]]:
        """Normalize heading levels into a legal tree (spec §7.3).

        Rules:
        1. First non-root heading becomes level 1 regardless of raw level.
        2. No level skipping: if jumping from 1 to 3, the 3 becomes 2.
        3. If document starts at level 2, everything shifts to start at 1.
        4. Normalization only affects ``BookNode.level``, not CONTENT.md.

        Args:
            headings: Raw headings.

        Returns:
            List of (raw_heading, normalized_level) tuples.
        """
        if not headings:
            return []

        result: list[tuple[_RawHeading, int]] = []
        # Map raw level → normalized level, built incrementally.
        level_map: dict[int, int] = {}
        prev_normalized = 0

        for h in headings:
            raw = h.raw_level
            if not level_map:
                # First heading → always level 1 (§7.3 #1).
                level_map[raw] = 1
                prev_normalized = 1
            elif raw in level_map:
                # Already seen this raw level → reuse mapping.
                prev_normalized = level_map[raw]
            elif raw > max(level_map.keys()):
                # New deeper level → previous normalized + 1 (no skipping, §7.3 #2-3).
                new_level = prev_normalized + 1
                level_map[raw] = new_level
                prev_normalized = new_level
            else:
                # New raw level that falls between already-seen raw levels.
                # Find the smallest seen raw level > current raw and use its
                # normalized level (the new level slots in below it).
                higher_raws = [r for r in level_map if r > raw]
                if higher_raws:
                    ceiling = min(higher_raws)
                    prev_normalized = level_map[ceiling]
                    level_map[raw] = prev_normalized
                else:
                    # Should not happen, but guard against it.
                    prev_normalized = 1
                    level_map[raw] = 1

            result.append((h, prev_normalized))

        return result

    # ------------------------------------------------------------------ tree building (§7.4)

    def _build_tree(
        self,
        book_id: str,
        content: str,
        normalized: list[tuple[_RawHeading, int]],
        book_title: str = "",
    ) -> list[BookNode]:
        """Build the BookNode tree from normalized headings.

        Args:
            book_id: Book id.
            content: Full CONTENT.md text.
            normalized: List of (heading, normalized_level) tuples.
            book_title: Book title for the root node (spec §3.3 #11).

        Returns:
            List of BookNode objects (root + all heading nodes).
        """
        from bookscout.core.lib.utils import gen_id

        content_len = len(content)
        root_id = gen_id(prefix="node_")
        root = BookNode(
            id=root_id,
            book_id=book_id,
            parent_id="",
            level=0,
            order_index=0,
            title=book_title,
            title_offset=0,
            title_length=0,
            content_offset=0,
            content_length=0,
        )

        if not normalized:
            # No headings → root covers entire content (§7.4 #8).
            root = dataclasses.replace(root, content_offset=0, content_length=content_len)
            return [root]

        nodes: list[BookNode] = [root]
        # Stack of (node_id, level) for parent tracking.
        stack: list[tuple[str, int]] = [(root_id, 0)]
        # Per-parent order counters.
        order_counter: dict[str, int] = {root_id: 0}

        for idx, (heading, level) in enumerate(normalized):
            # Pop stack until top level < current level.
            while len(stack) > 1 and stack[-1][1] >= level:
                stack.pop()
            parent_id = stack[-1][0]

            order = order_counter.get(parent_id, 0)
            order_counter[parent_id] = order + 1

            node_id = gen_id(prefix="node_")

            # Content range: from after this heading line to the start of
            # the next heading (at any level) or end of content.
            content_start = heading.line_end_offset
            if idx + 1 < len(normalized):
                next_heading = normalized[idx + 1][0]
                content_end = next_heading.line_offset
            else:
                content_end = content_len

            # Clamp content_end to content_len (the \n after the last line
            # may not exist).
            content_end = min(content_end, content_len)
            content_length = max(0, content_end - content_start)

            node = BookNode(
                id=node_id,
                book_id=book_id,
                parent_id=parent_id,
                level=level,
                order_index=order,
                title=heading.title,
                title_offset=heading.line_offset,
                title_length=heading.line_length,
                content_offset=content_start,
                content_length=content_length,
            )
            nodes.append(node)
            stack.append((node_id, level))

        # If root has no content of its own and there are headings, root's
        # content_length stays 0 (§7.4 #4). The first heading starts at its
        # own offset, so root content before the first heading is lost.
        # Fix: give root the text before the first heading as its content.
        first_heading_offset = normalized[0][0].line_offset
        if first_heading_offset > 0:
            root = dataclasses.replace(
                root,
                content_offset=0,
                content_length=first_heading_offset,
            )
            nodes[0] = root

        return nodes
