"""Exception hierarchy and error-handling decorator for bookscout-books.

Mirrors the pattern used by :mod:`bookscout.filestore.exceptions`: a single
base :class:`BooksError`, concrete subclasses per operation family, and a
``handle_errors`` decorator that wraps unexpected exceptions into
:class:`BooksError` (or a more specific subclass) while logging via the
owning :class:`bookscout.books.BooksStore`'s ``self.logger``.
"""

from __future__ import annotations

import inspect
import typing as t


class BooksError(Exception):
    """Base exception for all bookscout-books errors."""


class BookNotFoundError(BooksError):
    """Raised when a :class:`~bookscout.books.Book` is not found."""


class NodeNotFoundError(BooksError):
    """Raised when a :class:`~bookscout.books.BookNode` is not found."""


class BookExistsError(BooksError):
    """Raised when creating a book whose id already exists."""


class TreeValidationError(BooksError):
    """Raised when a :class:`~bookscout.books.BookNode` tree violates invariants.

    Covers the constraints in spec §3.5: missing/duplicate root, level
    regression, sibling order conflicts, cycles, out-of-range offsets, etc.
    """


class ContentError(BooksError):
    """Raised when ``CONTENT.md`` cannot be read or a node range is invalid."""


class StoreError(BooksError):
    """Raised for low-level store failures (I/O, schema, unexpected errors)."""


if t.TYPE_CHECKING:
    BooksMethodT = t.TypeVar("BooksMethodT", bound=t.Callable[..., t.Any])


@t.overload
def handle_errors(  # noqa: UP047
    method: BooksMethodT,
    *,
    exc_type: type[BooksError] = ...,
    msg: str | None = ...,
) -> BooksMethodT: ...
@t.overload
def handle_errors(
    method: None = ...,
    *,
    exc_type: type[BooksError] = ...,
    msg: str | None = ...,
) -> t.Callable[[BooksMethodT], BooksMethodT]: ...
def handle_errors(  # noqa: UP047
    method: BooksMethodT | None = None,
    *,
    exc_type: type[BooksError] = StoreError,
    msg: str | None = None,
) -> BooksMethodT | t.Callable[[BooksMethodT], BooksMethodT]:
    """Decorator wrapping store method errors into :class:`BooksError`.

    Async methods and async generators are both supported. Exceptions that are
    already :class:`BooksError` subclasses are re-raised unchanged.

    Args:
        method: The store method to wrap.
        exc_type: Exception type to raise for unexpected errors.
        msg: Optional custom message; defaults to a method-specific message.

    Returns:
        A wrapped method (or a decorator when used with arguments).
    """

    def decorator(method: BooksMethodT) -> BooksMethodT:
        if inspect.isasyncgenfunction(method):

            async def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
                try:
                    async for item in method(self, *args, **kwargs):
                        yield item
                except BooksError:
                    raise
                except Exception as e:
                    self.logger.error(msg or f"Error in store operation {method.__name__}: {e}")
                    raise exc_type(msg or f"An error occurred in store operation {method.__name__}: {e}") from e

        else:

            async def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:  # type: ignore[misc]
                try:
                    return await method(self, *args, **kwargs)
                except BooksError:
                    raise
                except Exception as e:
                    self.logger.error(msg or f"Error in store operation {method.__name__}: {e}")
                    raise exc_type(msg or f"An error occurred in store operation {method.__name__}: {e}") from e

        return t.cast("BooksMethodT", wrapper)

    if method is not None:
        return decorator(method)
    return decorator
