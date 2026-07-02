from __future__ import annotations

import builtins
import types
import typing as t


@t.runtime_checkable
class SyncResource(t.Protocol):
    """Protocol for synchronous stateful components managed by a context
    manager.

    Classes implementing this protocol often manage a resource (e.g., a
    connection pool, a cache client) whose lifecycle is tied to a main
    application block, typically handled via a standard context manager
    (`__enter__`/`__exit__`).
    """

    def startup(self) -> None:
        """Initializes the resource or service.

        This method should set up necessary internal state or external
        connections.
        """

    def shutdown(self) -> None:
        """Cleans up the resource or service.

        This method should safely close connections, flush buffers, or release
        locks.
        """


@t.runtime_checkable
class AsyncResource(t.Protocol):
    """Protocol for asynchronous stateful components managed by an async
    context manager.

    This is the asynchronous counterpart to :class:`SyncResource`, typically
    managed via an async context manager (`__aenter__`/`__aexit__`).
    """

    async def startup(self) -> None:
        """Asynchronously initializes the resource or service."""

    async def shutdown(self) -> None:
        """Asynchronously cleans up the resource or service."""


@t.runtime_checkable
class SyncLifecycleAware(t.Protocol):
    """Protocol for synchronous components that hook into the application
    lifecycle.

    Components implementing this protocol act as observers, responding to
    application-level startup and shutdown events without managing their own
    long-lived internal state.
    """

    def on_startup(self) -> None:
        """Called synchronously when the application or service starts up."""

    def on_shutdown(self) -> None:
        """Called synchronously when the application or service shuts down."""


@t.runtime_checkable
class AsyncLifecycleAware(t.Protocol):
    """Protocol for asynchronous components that hook into the application
    lifecycle.

    This is the asynchronous counterpart to :class:`SyncLifecycleAware`. These
    components respond to application-level events.
    """

    async def on_startup(self) -> None:
        """Called asynchronously when the application or service starts up."""

    async def on_shutdown(self) -> None:
        """Called asynchronously when the application or service shuts down."""


S_contra = t.TypeVar("S_contra", contravariant=True)
R_co = t.TypeVar("R_co", bound=t.AsyncIterable[t.Any], covariant=True)


@t.runtime_checkable
class SimplexSession(t.Protocol[S_contra, R_co]):
    """Protocol for an asynchronous simplex (one-way) session without internal
    state management.

    This is primarily used for communication channels (like server-sent events
    or one-way streaming RPCs) that are transiently created and destroyed, and
    only manage the communication flow within the scope of an async context
    manager.

    Attributes:
        session_id: A unique identifier for the current session instance.

    Example:
        ```python
        async with SimplexSession(...) as session:
            recv = session.send(data)
            async for message in recv:
                process(message)
        ```
    """

    session_id: str

    async def __aenter__(self) -> t.Self:
        """Asynchronously enters the runtime context related to this session."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Asynchronously exits the runtime context, cleaning up the session."""

    def send(self, data: S_contra) -> R_co:
        """Sends data asynchronously over the session.

        Args:
            data: The data payload to send.

        Returns:
            An asynchronous iterable yielding responses or acknowledgments.
        """


@t.runtime_checkable
class DuplexSession(t.Protocol[S_contra, R_co]):
    """Protocol for an asynchronous duplex (two-way) session without internal
    state management.

    This is primarily used for communication channels (like WebSockets or
    streaming RPCs) that are transiently created and destroyed, and only manage
    the communication flow within the scope of an async context manager.

    Attributes:
        sid: A unique identifier for the current session instance.

    Example:
        ```python
        async def send_coro(
            data_generator: AsyncIterable[S],
            session: DuplexSession[S, R],
        ):
            async for data in data_generator:
                await session.send(data)


        async def recv_coro(session: DuplexSession[S, R]):
            async for message in session.recv():
                process(message)


        async with DuplexSession(...) as session:
            send_task = asyncio.create_task(send_coro(data_gen, session))
            recv_task = asyncio.create_task(recv_coro(session))
            await asyncio.gather(send_task, recv_task)
        ```
    """

    sid: str

    async def __aenter__(self) -> t.Self:
        """Asynchronously enters the runtime context related to this session."""

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> None:
        """Asynchronously exits the runtime context, cleaning up the session."""

    async def send(self, data: S_contra) -> None:
        """Sends data asynchronously over the session.

        Args:
            data: The data payload to send.
        """

    def recv(self) -> R_co:
        """Retrieves an asynchronous iterable for receiving data from the
        session.

        Returns:
            An asynchronous iterable yielding received data.
        """


T_co = t.TypeVar("T_co", covariant=True)


@t.runtime_checkable
class SupportsRead(t.Protocol[T_co]):  # pylint: disable=too-few-public-methods
    """Protocol for a synchronous readable object (like a file or buffer)."""

    def read(self, length: int = ..., /) -> T_co:
        """Reads at most `length` bytes/items from the object.

        Args:
            length: The maximum number of bytes/items to read. Defaults to
                reading until EOF.

        Returns:
            The data read from the object.
        """


@t.runtime_checkable
class SupportsAsyncRead(t.Protocol[T_co]):  # pylint: disable=too-few-public-methods
    """Protocol for an asynchronous readable object."""

    async def read(self, length: int = ..., /) -> T_co:
        """Asynchronously reads at most `length` bytes/items from the object.

        Args:
            length: The maximum number of bytes/items to read. Defaults to
                reading until EOF.

        Returns:
            The data read from the object.
        """


class HealthCheckResult(t.TypedDict):
    """Result of a health check."""

    healthy: bool
    """Indicates if the component is healthy."""

    details: dict[str, t.Any]
    """Additional details about the health status."""


@t.runtime_checkable
class AsyncHealthCheckable(t.Protocol):
    """Protocol for components that can report their health status."""

    async def ping(self) -> HealthCheckResult | bool:
        """Checks the health status of the component.

        Returns:
            HealthCheckResult object or a boolean indicating health status
            (True for healthy, False for unhealthy).
        """


@t.runtime_checkable
class SyncHealthCheckable(t.Protocol):
    """Protocol for synchronous components that can report their health
    status."""

    def ping(self) -> HealthCheckResult | bool:
        """Checks the health status of the component.

        Returns:
            HealthCheckResult object or a boolean indicating health status
            (True for healthy, False for unhealthy).
        """


class BlobStore(t.Protocol):
    """Protocol for blob storage operations.

    This class defines the interface for storage backends, providing methods
    for uploading, downloading, deleting, and managing stored objects.
    Implementations include filesystem-backed and object-storage-backed stores.
    """

    async def upload(
        self,
        data: bytes | t.IO[bytes],
        key: str,
        metadata: t.Mapping[str, t.Any] | None = None,
        **kwargs: t.Any,
    ) -> str:
        """Upload data to storage.

        Args:
            data: The data to upload, either as bytes or a file-like object.
            key: The unique identifier for the stored object.
            metadata: Optional metadata to associate with the object.
            **kwargs: Additional storage-specific parameters.

        Returns:
            The key of the uploaded object.

        Raises:
            BlobStoreError: If the upload fails.
        """

    async def upload_multipart(
        self,
        parts: t.AsyncIterable[bytes],
        key: str,
        metadata: t.Mapping[str, t.Any] | None = None,
        **kwargs: t.Any,
    ) -> str:
        """Upload data in multiple parts.

        Args:
            parts: An async iterable of byte chunks to upload.
            key: The unique identifier for the stored object.
            metadata: Optional metadata to associate with the object.
            **kwargs: Additional storage-specific parameters.

        Returns:
            The key of the uploaded object.

        Raises:
            BlobStoreError: If the multipart upload fails.
        """

    async def get_metadata(self, key: str) -> builtins.dict[str, t.Any]:
        """Retrieve metadata for a stored object.

        Args:
            key: The unique identifier of the object.

        Returns:
            A dictionary containing the object's metadata.

        Raises:
            BlobStoreError: If the object doesn't exist or metadata retrieval fails.
        """

    async def download(
        self,
        key: str,
        *,
        stream: bool = False,
        chunk_size: int = 8192,
        **kwargs: t.Any,
    ) -> bytes | t.AsyncIterable[bytes]:
        """Download data from storage.

        Args:
            key: The unique identifier of the object to download.
            stream: If True, return an async iterable of chunks; otherwise return all bytes.
            chunk_size: Size of each chunk when streaming (in bytes).
            **kwargs: Additional storage-specific parameters.

        Returns:
            The object's data as bytes, or an async iterable of byte chunks if streaming.

        Raises:
            BlobStoreError: If the download fails or object doesn't exist.
        """

    async def delete(self, key: str) -> None:
        """Delete an object from storage.

        Args:
            key: The unique identifier of the object to delete.

        Raises:
            BlobStoreError: If the deletion fails.
        """

    def list(
        self,
        prefix: str = "",
        page_size: int = 10,
        **kwargs: t.Any,
    ) -> t.AsyncIterator[builtins.list[str]]:
        """List objects in storage with the given prefix.

        Args:
            prefix: Optional prefix to filter objects.
            page_size: Number of object keys to return per iteration.
            **kwargs: Additional storage-specific parameters.

        Yields:
            Lists of object keys matching the prefix.

        Raises:
            BlobStoreError: If listing fails.
        """

    async def exists(self, key: str) -> bool:
        """Check if an object exists in storage.

        Args:
            key: The unique identifier of the object.

        Returns:
            True if the object exists, False otherwise.

        Raises:
            BlobStoreError: If the existence check fails.
        """

    async def clear(self, prefix: str = "") -> None:
        """Delete all objects with the given prefix.

        Args:
            prefix: Optional prefix to filter objects for deletion.

        Raises:
            BlobStoreError: If the clear operation fails.
        """

    async def copy(self, source_key: str, dest_key: str, **kwargs: t.Any) -> str:
        """Copy an object to a new location.

        Args:
            source_key: The unique identifier of the source object.
            dest_key: The unique identifier for the destination object.
            **kwargs: Additional storage-specific parameters.

        Returns:
            The key of the copied object.

        Raises:
            BlobStoreError: If the copy operation fails.
        """
