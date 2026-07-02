"""BookScout ontology retrieval tools — BaseTool implementations for MCP exposure.

Each tool wraps a BooksStore method, making it callable by LLM agents
via the standard bookscout-tools BaseTool interface.
"""

from __future__ import annotations

import json
import typing as t
from typing import Annotated

from bookscout.tools import BaseTool
from bookscout.tools import Property

if t.TYPE_CHECKING:
    from bookscout.books import BooksStore


def _node_to_dict(node: t.Any) -> dict[str, t.Any]:
    """Convert a BookNode to a JSON-serializable dict."""
    return {
        "id": node.id,
        "book_id": node.book_id,
        "parent_id": node.parent_id,
        "level": node.level,
        "order_index": node.order_index,
        "title": node.title,
        "title_offset": node.title_offset,
        "title_length": node.title_length,
        "content_offset": node.content_offset,
        "content_length": node.content_length,
    }


def _book_to_dict(book: t.Any) -> dict[str, t.Any]:
    """Convert a Book to a JSON-serializable dict."""
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "isbn": book.isbn,
        "publisher": book.publisher,
        "language": book.language,
        "extras": book.extras,
        "content_path": book.content_path,
        "source_path": book.source_path,
        "checksum": book.checksum,
    }


class GetBookTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_book",
    description="Get a book by its ID. Returns book metadata including title, author, ISBN, publisher, language.",
):
    """Tool: get_book."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID to look up")],
    ) -> str:
        book = await self._store.get_book(book_id)
        return json.dumps(_book_to_dict(book), ensure_ascii=False)


class ListBooksTool(  # type: ignore[call-arg]
    BaseTool,
    name="list_books",
    description="List all books in the store. Returns an array of book metadata.",
):
    """Tool: list_books."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(self) -> str:
        books = await self._store.list_books()
        return json.dumps([_book_to_dict(b) for b in books], ensure_ascii=False)


class GetNodeTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_node",
    description="Get a single BookNode by its node ID. Returns node structure info including level, title, parent, content offsets.",
):
    """Tool: get_node."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        node_id: Annotated[str, Property(description="The node ID to look up")],
    ) -> str:
        node = await self._store.get_node(node_id)
        return json.dumps(_node_to_dict(node), ensure_ascii=False)


class GetRootNodeTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_root_node",
    description="Get the root node of a book's node tree. The root node has level=0 and represents the whole book.",
):
    """Tool: get_root_node."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
    ) -> str:
        node = await self._store.get_root_node(book_id)
        return json.dumps(_node_to_dict(node), ensure_ascii=False)


class GetChildrenTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_children",
    description="Get the direct children of a node, ordered by order_index. Returns an array of child nodes.",
):
    """Tool: get_children."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        node_id: Annotated[str, Property(description="The parent node ID")],
    ) -> str:
        children = await self._store.get_children(node_id)
        return json.dumps([_node_to_dict(c) for c in children], ensure_ascii=False)


class GetTreeTool(  # type: ignore[call-arg]
    BaseTool,
    name="get_tree",
    description="Get the complete node tree for a book in pre-order traversal. Returns an array of all nodes.",
):
    """Tool: get_tree."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
    ) -> str:
        tree = await self._store.get_tree(book_id)
        return json.dumps([_node_to_dict(n) for n in tree], ensure_ascii=False)


class ListNodesByLevelTool(  # type: ignore[call-arg]
    BaseTool,
    name="list_nodes_by_level",
    description="List all nodes at a specific level in a book's tree. E.g. level=1 lists all chapters, level=2 lists all sections.",
):
    """Tool: list_nodes_by_level."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        book_id: Annotated[str, Property(description="The book ID")],
        level: Annotated[
            int, Property(description="The node level to filter (0=root, 1=chapter, 2=section, etc.)", ge=0)
        ],
    ) -> str:
        tree = await self._store.get_tree(book_id)
        filtered = [n for n in tree if n.level == level]
        return json.dumps([_node_to_dict(n) for n in filtered], ensure_ascii=False)


class ReadNodeContentTool(  # type: ignore[call-arg]
    BaseTool,
    name="read_node_content",
    description="Read the own body text of a node from CONTENT.md. Returns the text content of the node (excluding children).",
):
    """Tool: read_node_content."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        node_id: Annotated[str, Property(description="The node ID")],
    ) -> str:
        content = await self._store.read_node_content(node_id)
        return content if content else "(empty)"


class ReadSubtreeContentTool(  # type: ignore[call-arg]
    BaseTool,
    name="read_subtree_content",
    description="Read the concatenated body text of a node and all its descendants (subtree content). Returns the full text of the subtree.",
):
    """Tool: read_subtree_content."""

    def __init__(self, store: BooksStore) -> None:
        self._store = store

    async def __call__(
        self,
        node_id: Annotated[str, Property(description="The root node ID of the subtree")],
    ) -> str:
        content = await self._store.read_subtree_content(node_id)
        return content if content else "(empty)"


def create_ontology_tools(store: BooksStore) -> list[BaseTool]:
    """Create all ontology retrieval tools bound to a BooksStore.

    Args:
        store: An initialized BooksStore instance.

    Returns:
        List of BaseTool instances for ontology operations.
    """
    return [
        GetBookTool(store),
        ListBooksTool(store),
        GetNodeTool(store),
        GetRootNodeTool(store),
        GetChildrenTool(store),
        GetTreeTool(store),
        ListNodesByLevelTool(store),
        ReadNodeContentTool(store),
        ReadSubtreeContentTool(store),
    ]


__all__ = [
    "GetBookTool",
    "GetChildrenTool",
    "GetNodeTool",
    "GetRootNodeTool",
    "GetTreeTool",
    "ListBooksTool",
    "ListNodesByLevelTool",
    "ReadNodeContentTool",
    "ReadSubtreeContentTool",
    "create_ontology_tools",
]
