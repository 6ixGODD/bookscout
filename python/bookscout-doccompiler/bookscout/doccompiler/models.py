"""Internal SQLModel tables for parser mapping databases.

These models are private to the doccompiler package. Each parser's mapping
store creates its own SQLite database with the relevant table.
"""

from __future__ import annotations

from sqlmodel import Field
from sqlmodel import SQLModel


class EpubMappingModel(SQLModel, table=True):
    """Persistent row for an EPUB → CONTENT.md mapping entry (spec §4.5).

    Attributes:
        id: Auto-increment primary key.
        book_id: Owning book id.
        content_offset/content_length: Range in ``CONTENT.md``.
        href: EPUB internal XHTML file path.
        spine_index: Position in the EPUB spine.
        element_tag: Source element tag (h1, p, li, ...).
        element_index: Sequential index of block elements in the XHTML file.
        element_id: XML id attribute; ``""`` when absent.
        element_path: Simplified XPath to the source element.
        parser_name/parser_version: Provenance.
    """

    __tablename__ = "epub_mappings"

    id: int | None = Field(default=None, primary_key=True)
    book_id: str = Field(index=True, nullable=False)
    content_offset: int = Field(nullable=False)
    content_length: int = Field(nullable=False)
    href: str = Field(nullable=False, index=True)
    spine_index: int = Field(nullable=False)
    element_tag: str = Field(nullable=False)
    element_index: int = Field(nullable=False)
    element_id: str = Field(default="", nullable=False)
    element_path: str = Field(default="", nullable=False)
    parser_name: str = Field(default="", nullable=False)
    parser_version: str = Field(default="", nullable=False)


class PdfMappingModel(SQLModel, table=True):
    """Persistent row for a PDF → CONTENT.md mapping entry (spec §4.4).

    Attributes:
        id: Auto-increment primary key.
        book_id: Owning book id.
        content_offset/content_length: Range in ``CONTENT.md``.
        page_index: PDF page index (0-based, global after batch correction).
        x0/y0/x1/y1: Bounding box in PDF parser coordinates.
        parser_name/parser_version: Provenance.
    """

    __tablename__ = "pdf_mappings"

    id: int | None = Field(default=None, primary_key=True)
    book_id: str = Field(index=True, nullable=False)
    content_offset: int = Field(nullable=False)
    content_length: int = Field(nullable=False)
    page_index: int = Field(nullable=False, index=True)
    x0: float = Field(default=0.0, nullable=False)
    y0: float = Field(default=0.0, nullable=False)
    x1: float = Field(default=0.0, nullable=False)
    y1: float = Field(default=0.0, nullable=False)
    parser_name: str = Field(default="", nullable=False)
    parser_version: str = Field(default="", nullable=False)
