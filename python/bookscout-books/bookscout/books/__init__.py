"""`bookscout.books` package — the BookScout ontology layer.

Defines :class:`Book` and :class:`BookNode` (immutable domain models) plus
:class:`BooksStore`, the single entry point for persisting and querying the
ontology. SQLite is an implementation detail of ``BooksStore`` and is never
exposed to callers.

See ``experimental/req/data-layer.md`` §2.1 and §3 for the full specification.
"""

from __future__ import annotations

from .exceptions import BookExistsError
from .exceptions import BookNotFoundError
from .exceptions import BooksError
from .exceptions import ContentError
from .exceptions import NodeNotFoundError
from .exceptions import StoreError
from .exceptions import TreeValidationError
from .store import BooksConfig
from .store import BooksStore
from .types import Book
from .types import BookNode

__all__ = [
    "Book",
    "BookExistsError",
    "BookNode",
    "BookNotFoundError",
    "BooksConfig",
    "BooksError",
    "BooksStore",
    "ContentError",
    "NodeNotFoundError",
    "StoreError",
    "TreeValidationError",
]
