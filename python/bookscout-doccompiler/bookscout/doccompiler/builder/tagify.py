"""Tagification of content chunks for LLM tool-driven building (spec §8.3).

Inserts ``<sN/>`` tags at sentence/punctuation/heading boundaries so the LLM
can reference precise character ranges via tag labels instead of raw offsets.
The tag map records each ``<sN/>`` → absolute CONTENT.md offset mapping.
"""

from __future__ import annotations

import dataclasses
import re

# Characters that trigger a tag insertion (after them).
_TAG_TRIGGERS = re.compile(r"[。！？\n#.!?;；:：]")
# Heading lines get a tag at end.
_HEADING_RE = re.compile(r"^(#{1,6})\s+.+$")


@dataclasses.dataclass(slots=True)
class TagMap:
    """Maps ``<sN/>`` tags to absolute CONTENT.md character offsets.

    Attributes:
        tags: Dict mapping tag number → absolute offset in CONTENT.md.
        tagged_text: The chunk text with ``<sN/>`` tags inserted.
        chunk_start: Absolute offset of the chunk start in CONTENT.md.
        chunk_end: Absolute offset of the chunk end in CONTENT.md.
    """

    tags: dict[int, int]
    tagged_text: str
    chunk_start: int
    chunk_end: int

    def resolve_range(self, start_tag: int, end_tag: int) -> tuple[int, int] | None:
        """Resolve a ``[start_tag, end_tag)`` range to absolute offsets.

        The range covers from the offset of ``start_tag`` to the offset of
        ``end_tag`` (exclusive).

        Args:
            start_tag: The ``<sN/>`` tag number at the range start.
            end_tag: The ``<sN/>`` tag number at the range end (exclusive).

        Returns:
            Tuple of (absolute_start, absolute_end) or ``None`` if either
            tag is not in the map.
        """
        if start_tag not in self.tags or end_tag not in self.tags:
            return None
        return self.tags[start_tag], self.tags[end_tag]

    def resolve_single(self, tag: int) -> int | None:
        """Resolve a single tag to its absolute offset.

        Args:
            tag: The ``<sN/>`` tag number.

        Returns:
            The absolute offset, or ``None`` if the tag is not in the map.
        """
        return self.tags.get(tag)


def tagify_chunk(chunk_text: str, chunk_start: int) -> TagMap:
    """Insert ``<sN/>`` tags into a chunk at boundaries.

    Tags are inserted:
    - At the start of the chunk (tag 0).
    - After each sentence-ending punctuation (。！？.!?).
    - After each newline.
    - After heading markers (# ...).

    Args:
        chunk_text: The raw chunk text from CONTENT.md.
        chunk_start: Absolute offset of this chunk's start in CONTENT.md.

    Returns:
        A :class:`TagMap` with the tag→offset mapping and tagged text.
    """
    tags: dict[int, int] = {}
    tagged_parts: list[str] = []
    tag_num = 0
    # Track position in the original chunk text.
    pos = 0

    # Tag 0 at chunk start.
    tags[0] = chunk_start
    tagged_parts.append("<s0/>")
    tag_num = 1

    for i, char in enumerate(chunk_text):
        tagged_parts.append(char)
        pos = i + 1

        # Insert a tag after this character if it's a boundary.
        is_boundary = False
        if char in "。！？.!?;；:：\n":
            is_boundary = True
        elif char == "#" and (i == 0 or chunk_text[i - 1] == "\n"):
            # Heading start — don't tag here, tag after the heading line.
            pass

        if is_boundary:
            tags[tag_num] = chunk_start + pos
            tagged_parts.append(f"<s{tag_num}/>")
            tag_num += 1

    # Final tag at chunk end.
    tags[tag_num] = chunk_start + len(chunk_text)
    tagged_parts.append(f"<s{tag_num}/>")

    return TagMap(
        tags=tags,
        tagged_text="".join(tagged_parts),
        chunk_start=chunk_start,
        chunk_end=chunk_start + len(chunk_text),
    )
