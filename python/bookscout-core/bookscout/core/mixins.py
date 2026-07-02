from __future__ import annotations

import time
import typing as t

if t.TYPE_CHECKING:
    import types


class AsyncResourceMixin:
    """Mixin class for managing asynchronous resource lifecycle.

    This mixin provides async context manager support and tracks resource
    uptime. Subclasses should implement the startup() and shutdown() methods to
    define their resource initialisation and clean-up logic.

    Attributes:
        _startup_time: The timestamp when the resource was started, or None if
            not started.
    """

    def __init__(self, *args: t.Any, **kwargs: t.Any):
        """Initialises the async resource mixin.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments.
        """
        super().__init__(*args, **kwargs)
        self._startup_time: float | None = None

    async def startup(self) -> None:
        """Starts up the resource and records the startup time.

        This method should be overridden by subclasses to implement
        resource-specific initialisation logic.
        """
        self._startup_time = time.perf_counter()

    async def shutdown(self) -> None:
        """Shuts down the resource and clears the startup time.

        This method should be overridden by subclasses to implement
        resource-specific clean-up logic.
        """
        self._startup_time = None

    @property
    def uptime(self) -> float:
        """Returns the uptime of the resource in seconds.

        Returns:
            The number of seconds since startup, or 0.0 if not started.
        """
        if self._startup_time is None:
            return 0.0
        return time.perf_counter() - self._startup_time

    async def __aenter__(self) -> t.Self:
        """Async context manager entry.

        Returns:
            Self instance after startup.
        """
        await self.startup()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> t.Literal[False]:
        """Async context manager exit.

        Args:
            exc_type: The exception type if an exception occurred.
            exc_val: The exception value if an exception occurred.
            traceback: The traceback if an exception occurred.

        Returns:
            False to propagate any exception that occurred.
        """
        await self.shutdown()
        return False


class SyncResourceMixin:
    """Mixin class for managing synchronous resource lifecycle.

    This mixin provides sync context manager support and tracks resource uptime.
    Subclasses should implement the startup() and shutdown() methods to define
    their resource initialisation and clean-up logic.

    Attributes:
        _startup_time: The timestamp when the resource was started, or None if
            not started.
    """

    def __init__(self, *args: t.Any, **kwargs: t.Any):
        super().__init__(*args, **kwargs)
        self._startup_time: float | None = None

    def startup(self) -> None:
        """Starts up the resource and records the startup time.

        This method should be overridden by subclasses to implement
        resource-specific initialisation logic.
        """
        self._startup_time = time.perf_counter()

    def shutdown(self) -> None:
        """Shuts down the resource and clears the startup time.

        This method should be overridden by subclasses to implement
        resource-specific clean-up logic.
        """
        self._startup_time = None

    @property
    def uptime(self) -> float:
        """Returns the uptime of the resource in seconds.

        Returns:
            The number of seconds since startup, or 0.0 if not started.
        """
        if self._startup_time is None:
            return 0.0
        return time.perf_counter() - self._startup_time

    def __enter__(self) -> t.Self:
        """Context manager entry.

        Returns:
            Self instance after startup.
        """
        self.startup()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        traceback: types.TracebackType | None,
    ) -> t.Literal[False]:
        """Context manager exit.

        Args:
            exc_type: The exception type if an exception occurred.
            exc_val: The exception value if an exception occurred.
            traceback: The traceback if an exception occurred.

        Returns:
            False to propagate any exception that occurred.
        """
        self.shutdown()
        return False


class AsyncLifecycleAwareMixin:
    """Mixin class for async lifecycle event hooks.

    This mixin provides hooks for startup and shutdown events in an async
    context. Subclasses can override these methods to perform actions during
    lifecycle events.
    """

    async def on_startup(self) -> None:
        """Hook called during startup.

        Override this method to perform custom initialisation logic.
        """

    async def on_shutdown(self) -> None:
        """Hook called during shutdown.

        Override this method to perform custom clean-up logic.
        """


class SyncLifecycleAwareMixin:
    """Mixin class for sync lifecycle event hooks.

    This mixin provides hooks for startup and shutdown events in a sync context.
    Subclasses can override these methods to perform actions during lifecycle
    events.
    """

    def on_startup(self) -> None:
        """Hook called during startup.

        Override this method to perform custom initialisation logic.
        """

    def on_shutdown(self) -> None:
        """Hook called during shutdown.

        Override this method to perform custom clean-up logic.
        """
