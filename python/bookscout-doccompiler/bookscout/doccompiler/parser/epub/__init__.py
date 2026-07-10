# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""EPUB parser 鈥?local implementation using EbookLib + lxml (spec 搂5.4).

Reads an EPUB file, extracts metadata, follows the spine in order, converts
each XHTML document to Markdown, and records element-level source mappings
into an EPUB mapping SQLite database.
"""

from __future__ import annotations

import dataclasses
import hashlib
import pathlib
import typing as t

import aiofiles
from ebooklib import ITEM_DOCUMENT
from ebooklib import epub
from lxml import html as lxml_html

from bookscout.doccompiler.parser import DocParser
from bookscout.doccompiler.types import EpubSourceMapping
from bookscout.doccompiler.types import ParserResult
from bookscout.doccompiler.types import SourceInfo
from bookscout.doccompiler.workspace import BookWorkspace

from .mapping_store import EpubMappingStore

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

PARSER_NAME = "EpubParser"
PARSER_VERSION = "0.1.0"

# Block-level HTML tags that generate a mapping entry.
_BLOCK_TAGS: frozenset[str] = frozenset({
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "p",
    "blockquote",
    "pre",
    "li",
    "dt",
    "dd",
    "figure",
    "figcaption",
    "caption",
})

# Tags whose children are processed but the tag itself adds no markdown.
_PASS_THROUGH_TAGS: frozenset[str] = frozenset({
    "html",
    "body",
    "head",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "nav",
    "aside",
    "ul",
    "ol",
    "dl",
    "table",
    "tbody",
    "thead",
    "tfoot",
    "tr",
})


@dataclasses.dataclass(slots=True)
class _ElementMapping:
    """Intermediate mapping collected during XHTML鈫抦arkdown conversion."""

    offset: int
    length: int
    tag: str
    element_index: int
    element_id: str
    element_path: str


class EpubParser(DocParser):
    """EPUB parser using EbookLib for structure and lxml for XHTML.

    Converts EPUB 鈫?``CONTENT.md`` with per-element source mappings.
    """

    async def parse(
        self,
        source_path: pathlib.Path,
        book_id: str,
        workspace: BookWorkspace,
    ) -> ParserResult:
        """Parse an EPUB file into CONTENT.md + EPUB mapping SQLite.

        Args:
            source_path: Path to the ``.epub`` file.
            book_id: The book id.
            workspace: The book workspace for output artifacts.

        Returns:
            A :class:`ParserResult`.
        """
        self.logger.info("epub parse starting", source=str(source_path), book_id=book_id)

        # Copy source into workspace.
        source_copy = workspace.source_file_path("original.epub")
        async with aiofiles.open(source_path, "rb") as src, aiofiles.open(source_copy, "wb") as dst:
            await dst.write(await src.read())

        checksum = await self._sha256(source_path)
        file_size = source_path.stat().st_size
        source_info = SourceInfo(
            book_id=book_id,
            source_type="epub",
            file_path=str(source_copy),
            file_size=file_size,
            checksum=checksum,
        )
        self.logger.info("source info", checksum=checksum, size=file_size)

        # Read EPUB structure.
        book = epub.read_epub(str(source_path), options={"ignore_ncx": True})
        metadata = self._extract_metadata(book)
        self.logger.info(
            "epub metadata",
            title=metadata.get("title", ""),
            author=metadata.get("author", ""),
        )

        # Build spine-ordered list of (spine_index, href, item).
        spine_items = self._get_spine_items(book)
        self.logger.info("spine items", count=len(spine_items))

        # Convert each spine item to markdown, collecting mappings.
        content_parts: list[str] = []
        all_mappings: list[EpubSourceMapping] = []
        current_offset = 0

        for spine_index, href, item in spine_items:
            xhtml_bytes = item.get_content()
            md_text, elem_mappings = self._convert_xhtml(xhtml_bytes, href, spine_index)
            if not md_text:
                continue
            content_parts.append(md_text)
            for em in elem_mappings:
                all_mappings.append(
                    EpubSourceMapping(
                        book_id=book_id,
                        content_offset=current_offset + em.offset,
                        content_length=em.length,
                        href=href,
                        spine_index=spine_index,
                        element_tag=em.tag,
                        element_index=em.element_index,
                        element_id=em.element_id,
                        element_path=em.element_path,
                        parser_name=PARSER_NAME,
                        parser_version=PARSER_VERSION,
                    )
                )
            current_offset += len(md_text)
            self.logger.debug("spine item converted", href=href, spine_index=spine_index, chars=len(md_text))

        content = "".join(content_parts)

        # Write CONTENT.md.
        content_path = workspace.content_path
        async with aiofiles.open(content_path, "w", encoding="utf-8") as f:
            await f.write(content)
        self.logger.info("CONTENT.md written", path=str(content_path), chars=len(content))

        # Write mapping SQLite.
        mapping_db = workspace.mapping_db_path("epub")
        store = EpubMappingStore(logger=self.logger, db_path=mapping_db)
        await store.startup()
        try:
            await store.write_mappings(all_mappings)
        finally:
            await store.shutdown()
        self.logger.info("epub mapping db written", path=str(mapping_db), mappings=len(all_mappings))

        return ParserResult(
            book_id=book_id,
            source_info=source_info,
            content_path=str(content_path),
            mapping_db_path=str(mapping_db),
            metadata=metadata,
        )

    # ------------------------------------------------------------------ helpers

    @staticmethod
    async def _sha256(path: pathlib.Path) -> str:
        """Compute sha256 hex of a file."""
        hasher = hashlib.sha256()
        async with aiofiles.open(path, "rb") as f:
            while True:
                chunk = await f.read(8192)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _extract_metadata(book: epub.EpubBook) -> dict[str, t.Any]:
        """Extract Dublin Core metadata from an EPUB.

        Returns a dict with keys ``title``, ``author``, ``language``,
        ``publisher``, ``isbn``, and any extra fields under ``extras``.
        """

        def _first(meta_result: t.Any) -> str:
            if not meta_result:
                return ""
            first = meta_result[0]
            if isinstance(first, tuple):
                return str(first[0]) if first[0] else ""
            return str(first)

        title = _first(book.get_metadata("DC", "title"))
        author = _first(book.get_metadata("DC", "creator"))
        language = _first(book.get_metadata("DC", "language"))
        publisher = _first(book.get_metadata("DC", "publisher"))
        raw_ids = book.get_metadata("DC", "identifier")
        isbn = ""
        for val in raw_ids or []:
            text = str(val[0]) if isinstance(val, tuple) else str(val)
            if "isbn" in text.lower():
                isbn = text
                break
        if not isbn and raw_ids:
            isbn = _first(raw_ids)

        extras: dict[str, t.Any] = {}
        # Collect extra DC fields not covered by the fixed schema.
        for field in ("date", "description", "subject", "rights", "source", "relation", "coverage", "contributor"):
            val = book.get_metadata("DC", field)
            if val:
                extras[field] = _first(val)

        return {
            "title": title,
            "author": author,
            "language": language,
            "publisher": publisher,
            "isbn": isbn,
            "extras": extras,
        }

    @staticmethod
    def _get_spine_items(book: epub.EpubBook) -> list[tuple[int, str, t.Any]]:
        """Return spine-ordered list of (spine_index, href, item).

        Only ITEM_DOCUMENT items that appear in the spine are included.
        """
        items_by_id: dict[str, t.Any] = {}
        for item in book.get_items_of_type(ITEM_DOCUMENT):
            items_by_id[item.get_id()] = item

        result: list[tuple[int, str, t.Any]] = []
        for idx, entry in enumerate(book.spine):
            idref = entry[0] if isinstance(entry, (tuple | list)) else entry
            item = items_by_id.get(idref)
            if item is None:
                continue
            result.append((idx, item.get_name(), item))
        return result

    def _convert_xhtml(
        self,
        xhtml_bytes: bytes,
        href: str,
        spine_index: int,  # pylint: disable=unused-argument
    ) -> tuple[str, list[_ElementMapping]]:
        """Convert one XHTML document to markdown text + element mappings.

        Args:
            xhtml_bytes: Raw XHTML content bytes.
            href: The EPUB internal href for this document.
            spine_index: The spine position of this document.

        Returns:
            A tuple of (markdown_text, element_mappings).
        """
        try:
            doc = lxml_html.fromstring(xhtml_bytes)
        except Exception as e:  # pylint: disable=broad-exception-caught
            self.logger.warning("xhtml parse failed, skipping", href=href, error=str(e))
            return "", []

        body = doc.find("body")
        if body is None:
            body = doc

        converter = _MarkdownConverter(logger=self.logger, href=href)
        converter.process_children(body)
        return converter.get_result()

    # Reverse-lookup convenience ---------------------------------------------

    async def lookup_source(
        self,
        mapping_db_path: pathlib.Path | str,
        content_offset: int,
    ) -> list[EpubSourceMapping]:
        """Open a mapping DB and look up mappings covering ``content_offset``.

        Args:
            mapping_db_path: Path to the EPUB mapping SQLite.
            content_offset: Character offset in ``CONTENT.md``.

        Returns:
            Matching EPUB source mappings.
        """
        store = EpubMappingStore(logger=self.logger, db_path=pathlib.Path(mapping_db_path))
        await store.startup()
        try:
            return await store.lookup(content_offset)
        finally:
            await store.shutdown()


class _MarkdownConverter:
    """Converts an lxml XHTML body to Markdown, tracking element offsets.

    For each block-level element, records the ``[offset, offset+length)``
    range in the generated markdown text. Inline formatting (bold, italic,
    links) is applied within block text.
    """

    def __init__(self, logger: Logger, href: str) -> None:
        self._logger = logger
        self._href = href
        self._buf: list[str] = []
        self._offset = 0
        self._mappings: list[_ElementMapping] = []
        self._element_index = 0
        # Counter per tag for XPath-like path generation.
        self._tag_counts: dict[str, int] = {}

    def process_children(self, parent: t.Any) -> None:
        """Process all child elements of ``parent`` in document order."""
        for child in parent:
            self._process_element(child, path_parts=[])

    def _process_element(self, elem: t.Any, path_parts: list[str]) -> None:
        """Process a single element, dispatching by tag type."""
        tag = self._strip_ns(elem.tag)
        if not isinstance(tag, str):
            return  # type: ignore[unreachable]

        # Build path.
        self._tag_counts[tag] = self._tag_counts.get(tag, 0) + 1
        current_path = [*path_parts, f"{tag}[{self._tag_counts[tag]}]"]

        if tag in _BLOCK_TAGS:
            self._convert_block(elem, tag, current_path)
        elif tag in _PASS_THROUGH_TAGS:
            for child in elem:
                self._process_element(child, current_path)
        elif tag in ("img", "image"):
            self._append_image(elem)
        elif tag == "hr":
            self._append("---\n\n")
        elif tag in ("br",):
            self._append_text("\n")
        elif tag in ("strong", "b", "em", "i", "a", "span", "code", "sub", "sup", "small", "mark", "abbr"):
            # Inline element at block level 鈥?treat as text.
            text = self._extract_inline(elem)
            if text.strip():
                self._append_text(f"{text}\n\n")
        else:
            self._logger.warning(
                "unhandled element, processing children",
                href=self._href,
                tag=tag,
            )
            for child in elem:
                self._process_element(child, current_path)

    def _convert_block(self, elem: t.Any, tag: str, current_path: list[str]) -> None:
        """Convert a block-level element to markdown and record its mapping."""
        start = self._offset

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            text = self._extract_inline(elem).strip()
            if text:
                self._append_text(f"{'#' * level} {text}\n\n")
        elif tag == "p":
            text = self._extract_inline(elem).strip()
            if text:
                self._append_text(f"{text}\n\n")
        elif tag == "blockquote":
            inner = self._extract_block_content(elem).strip()
            if inner:
                lines = inner.split("\n")
                quoted = "\n".join(f"> {line}" if line else ">" for line in lines)
                self._append_text(f"{quoted}\n\n")
        elif tag == "pre":
            code = self._extract_text(elem)
            self._append_text(f"```\n{code}\n```\n\n")
        elif tag == "li":
            text = self._extract_inline(elem).strip()
            if text:
                self._append_text(f"- {text}\n")
        elif tag in ("dt", "dd", "figcaption", "caption", "figure"):
            text = self._extract_inline(elem).strip()
            if text:
                self._append_text(f"{text}\n\n")
        else:
            text = self._extract_inline(elem).strip()
            if text:
                self._append_text(f"{text}\n\n")

        end = self._offset
        if end > start:
            elem_id = elem.get("id", "") or ""
            self._mappings.append(
                _ElementMapping(
                    offset=start,
                    length=end - start,
                    tag=tag,
                    element_index=self._element_index,
                    element_id=elem_id,
                    element_path="/" + "/".join(current_path),
                )
            )
            self._element_index += 1

    def _extract_block_content(self, elem: t.Any) -> str:
        """Extract text from block-level children (for blockquote inner content)."""
        parts: list[str] = []
        for child in elem:
            tag = self._strip_ns(child.tag)
            if not isinstance(tag, str):
                continue  # type: ignore[unreachable]
            if tag in _BLOCK_TAGS:
                text = self._extract_inline(child).strip()
                if text:
                    parts.append(text)
            else:
                text = self._extract_inline(child).strip()
                if text:
                    parts.append(text)
        return "\n".join(parts)

    def _extract_inline(self, elem: t.Any) -> str:
        """Recursively extract inline text with markdown formatting."""
        parts: list[str] = []
        if elem.text:
            parts.append(elem.text)
        for child in elem:
            tag = self._strip_ns(child.tag)
            if not isinstance(tag, str):
                if child.tail:  # type: ignore[unreachable]
                    parts.append(child.tail)
                continue
            if tag in ("strong", "b"):
                parts.append(f"**{self._extract_inline(child)}**")
            elif tag in ("em", "i"):
                parts.append(f"*{self._extract_inline(child)}*")
            elif tag == "a":
                href = child.get("href", "")
                text = self._extract_inline(child)
                parts.append(f"[{text}]({href})" if href else text)
            elif tag == "br":
                parts.append("\n")
            elif tag == "code":
                parts.append(f"`{self._extract_text(child)}`")
            elif tag in ("img", "image"):
                alt = child.get("alt", "")
                src = child.get("src", child.get("xlink:href", ""))
                parts.append(f"![{alt}]({src})" if src else "")
            elif tag in ("sub", "sup", "small", "mark", "span", "abbr", "cite", "q"):
                parts.append(self._extract_inline(child))
            else:
                parts.append(self._extract_inline(child))
            if child.tail:
                parts.append(child.tail)
        return "".join(parts)

    @staticmethod
    def _extract_text(elem: t.Any) -> str:
        """Extract all text content (no formatting)."""
        return elem.text_content() if hasattr(elem, "text_content") else "".join(elem.itertext())

    def _append_image(self, elem: t.Any) -> None:
        alt = elem.get("alt", "")
        src = elem.get("src", elem.get("xlink:href", ""))
        if src:
            self._append_text(f"![{alt}]({src})\n\n")

    def _append(self, text: str) -> None:
        self._buf.append(text)
        self._offset += len(text)

    def _append_text(self, text: str) -> None:
        self._append(text)

    @staticmethod
    def _strip_ns(tag: t.Any) -> str:
        """Strip XML namespace from a tag: ``{ns}tag`` 鈫?``tag``."""
        if not isinstance(tag, str):
            return str(tag)
        if "}" in tag:
            return tag.split("}", 1)[1]
        return tag

    def get_result(self) -> tuple[str, list[_ElementMapping]]:
        """Return (markdown_text, element_mappings)."""
        return "".join(self._buf), self._mappings
