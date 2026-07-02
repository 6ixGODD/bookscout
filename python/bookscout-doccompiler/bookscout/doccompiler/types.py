"""Public types for the doccompiler parser layer.

Defines the cross-parser data structures: :class:`SourceInfo`,
:class:`ParserResult`, and the per-format source mapping dataclasses
(:class:`PdfSourceMapping`, :class:`EpubSourceMapping`).

See ``experimental/req/data-layer.md`` §4.3 (SourceInfo), §4.4 (PDF mapping),
§4.5 (EPUB mapping), and §5.2 (ParserResult).
"""

from __future__ import annotations

import dataclasses
import typing as t


@dataclasses.dataclass(frozen=True, slots=True)
class SourceInfo:
    """Basic metadata for a source document (spec §4.3).

    Attributes:
        book_id: Owning book id.
        source_type: One of ``"pdf"``, ``"epub"``, ``"md"``, ``"txt"``.
        file_path: Path to the source file under filestore/workspace.
        file_size: File size in bytes.
        checksum: Source-file checksum (sha256 hex).
    """

    book_id: str
    source_type: str
    file_path: str
    file_size: int
    checksum: str


@dataclasses.dataclass(frozen=True, slots=True)
class ParserResult:
    """Unified output of every parser (spec §5.2).

    Attributes:
        book_id: Owning book id.
        source_info: Source document metadata.
        content_path: Path to the generated ``CONTENT.md``.
        mapping_db_path: Path to the parser's mapping SQLite database.
        metadata: Extra parser-provided metadata (e.g. EPUB dc:title).
    """

    book_id: str
    source_info: SourceInfo
    content_path: str
    mapping_db_path: str
    metadata: dict[str, t.Any]


@dataclasses.dataclass(frozen=True, slots=True)
class PdfSourceMapping:
    """A single PDF → CONTENT.md mapping entry (spec §4.4).

    Attributes:
        book_id: Owning book id.
        content_offset: Character offset in ``CONTENT.md``.
        content_length: Character length in ``CONTENT.md``.
        page_index: PDF page index (0-based, global after batch correction).
        x0/y0/x1/y1: Bounding box in PDF parser coordinates.
        parser_name: Name of the parser that produced this mapping.
        parser_version: Version of the parser.
    """

    book_id: str
    content_offset: int
    content_length: int
    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float
    parser_name: str
    parser_version: str


@dataclasses.dataclass(frozen=True, slots=True)
class EpubSourceMapping:
    """A single EPUB → CONTENT.md mapping entry (spec §4.5).

    Attributes:
        book_id: Owning book id.
        content_offset: Character offset in ``CONTENT.md``.
        content_length: Character length in ``CONTENT.md``.
        href: EPUB internal XHTML file path.
        spine_index: Position in the EPUB spine.
        element_tag: Source element tag (e.g. ``"h1"``, ``"p"``).
        element_index: Sequential index of this element in the XHTML document.
        element_id: XML/HTML element id; ``""`` when absent.
        element_path: Locatable path expression (simplified XPath).
        parser_name: Name of the parser that produced this mapping.
        parser_version: Version of the parser.
    """

    book_id: str
    content_offset: int
    content_length: int
    href: str
    spine_index: int
    element_tag: str
    element_index: int
    element_id: str
    element_path: str
    parser_name: str
    parser_version: str
