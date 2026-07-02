from __future__ import annotations

import inspect
import typing as t


class FileStoreError(Exception):
    """Base exception for filestore-related errors."""


class UploadError(FileStoreError):
    """Exception raised for errors during upload operations."""


class FetchError(FileStoreError):
    """Exception raised for errors during fetch operations."""


class DownloadError(FileStoreError):
    """Exception raised for errors during download operations."""


class DeleteError(FileStoreError):
    """Exception raised for errors during delete operations."""


class CopyError(FileStoreError):
    """Exception raised for errors during copy operations."""


class PresignError(FileStoreError):
    """Exception raised for errors during presign operations."""


class IndexError(FileStoreError):  # pylint: disable=redefined-builtin
    """Exception raised for errors during index/reconcile operations."""


class IntegrityError(FileStoreError):
    """Exception raised when a content checksum verification fails."""


class ConflictError(FileStoreError):
    """Exception raised on key conflicts or invariant violations."""


FileStoreMethodT = t.TypeVar("FileStoreMethodT", bound=t.Callable[..., t.Any])


@t.overload
def handle_errors(  # noqa: UP047
    method: FileStoreMethodT,
    *,
    exc_type: type[FileStoreError] = ...,
    msg: str | None = ...,
) -> FileStoreMethodT: ...
@t.overload
def handle_errors(
    method: None = ...,
    *,
    exc_type: type[FileStoreError] = ...,
    msg: str | None = ...,
) -> t.Callable[[FileStoreMethodT], FileStoreMethodT]: ...
def handle_errors(  # noqa: UP047
    method: FileStoreMethodT | None = None,
    *,
    exc_type: type[FileStoreError] = FileStoreError,
    msg: str | None = None,
) -> FileStoreMethodT | t.Callable[[FileStoreMethodT], FileStoreMethodT]:
    """Decorator to handle filestore-related errors and re-raise them as
    :class:`FileStoreError`.

    Supports both regular async methods and async generator methods.
    Already-typed :class:`FileStoreError` exceptions are re-raised without
    wrapping.

    Args:
        method: The store method to wrap.
        exc_type: The exception type to raise on error.
        msg: Optional custom message for the exception.

    Returns:
        A wrapped method that raises :class:`FileStoreError` on exceptions.
    """

    def decorator(method: FileStoreMethodT) -> FileStoreMethodT:
        if inspect.isasyncgenfunction(method):

            async def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
                try:
                    async for item in method(self, *args, **kwargs):
                        yield item
                except FileStoreError:
                    raise
                except Exception as e:
                    self.logger.error(msg or f"Error in store operation {method.__name__}: {e}")
                    raise exc_type(msg or f"An error occurred in store operation {method.__name__}: {e}") from e

        else:

            async def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:  # type: ignore[misc]
                try:
                    return await method(self, *args, **kwargs)
                except FileStoreError:
                    raise
                except Exception as e:
                    self.logger.error(msg or f"Error in store operation {method.__name__}: {e}")
                    raise exc_type(msg or f"An error occurred in store operation {method.__name__}: {e}") from e

        return t.cast(FileStoreMethodT, wrapper)

    if method is not None:
        return decorator(method)
    return decorator


def handle_sync_errors(  # noqa: UP047
    method: FileStoreMethodT | None = None,
    *,
    exc_type: type[FileStoreError] = FileStoreError,
    msg: str | None = None,
) -> FileStoreMethodT | t.Callable[[FileStoreMethodT], FileStoreMethodT]:
    """Decorator to handle filestore-related errors in synchronous methods and
    re-raise them as :class:`FileStoreError`.

    Already-typed :class:`FileStoreError` exceptions are re-raised without
    wrapping.

    Args:
        method: The store method to wrap.
        exc_type: The exception type to raise on error.
        msg: Optional custom message for the exception.

    Returns:
        A wrapped method that raises :class:`FileStoreError` on exceptions.
    """

    def decorator(method: FileStoreMethodT) -> FileStoreMethodT:
        def wrapper(self: t.Any, *args: t.Any, **kwargs: t.Any) -> t.Any:
            try:
                return method(self, *args, **kwargs)
            except FileStoreError:
                raise
            except Exception as e:
                self.logger.error(msg or f"Error in store operation {method.__name__}: {e}")
                raise exc_type(msg or f"An error occurred in store operation {method.__name__}: {e}") from e

        return t.cast(FileStoreMethodT, wrapper)

    if method is not None:
        return decorator(method)
    return decorator
