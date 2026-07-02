from __future__ import annotations

import contextvars as cvs
import types
import typing as t

context: cvs.ContextVar[Context | None] = cvs.ContextVar("current_context", default=None)


class Context(t.MutableMapping[t.Hashable, t.Any]):
    """Request-scoped context storage.

    Usage:
        ```python
        # In middleware/request handler
        async with Context({"user_id": "123", "request_id": "abc"}):
            # Within this context, get current context
            ctx = Context.current()
            user_id = ctx["user_id"]

            # Nested contexts inherit parent data
            async with Context({"role": "admin"}):
                ctx2 = Context.current()
                print(ctx2["user_id"])  # Still accessible: "123"
                print(ctx2["role"])  # New data: "admin"
        ```
    """

    def __init__(self, initial_data: dict[t.Hashable, t.Any] | None = None):
        """Initialize a new Context instance.

        Args:
            initial_data: Initial context values.
        """
        self._data: dict[t.Hashable, t.Any] = (initial_data or {}).copy()
        self._token: cvs.Token[Context | None] | None = None

    async def __aenter__(self) -> t.Self:
        """Enter context and register as current."""
        # Get parent context data (if any)
        parent = context.get()
        if parent is not None:
            # Inherit parent data
            self._data = {**parent._data, **self._data}

        # Register self as current
        self._token = context.set(self)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        """Exit context and restore parent."""
        if self._token is not None:
            context.reset(self._token)
            self._token = None

    @staticmethod
    def current() -> Context:
        """Get the current context.

        Returns:
            Current Context instance.

        Raises:
            RuntimeError: If no context is active.
        """
        ctx = context.get()
        if ctx is None:
            raise RuntimeError("No active context. Use 'async with Context(...)'")
        return ctx

    @staticmethod
    def try_current() -> Context | None:
        """Try to get the current context.

        Returns:
            Current Context instance or None if no context is active.
        """
        return context.get()

    # Dictionary interface - operate on instance data
    def __getitem__(self, key: t.Hashable) -> t.Any:
        return self._data[key]

    def __setitem__(self, key: t.Hashable, value: t.Any) -> None:
        self._data[key] = value

    def __delitem__(self, key: t.Hashable) -> None:
        del self._data[key]

    def __iter__(self) -> t.Iterator[t.Hashable]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"Context(data={self._data})"

    # Convenience methods
    def get(self, key: t.Hashable, default: t.Any = None) -> t.Any:
        return self._data.get(key, default)

    def set(self, key: t.Hashable, value: t.Any) -> t.Self:
        self._data[key] = value
        return self

    def setx(self, **kwargs: t.Any) -> t.Self:
        self._data.update(kwargs)
        return self

    def delete(self, key: t.Hashable) -> t.Self:
        self._data.pop(key, None)
        return self

    def clear(self) -> None:
        self._data.clear()

    def copy(self) -> dict[t.Hashable, t.Any]:
        return self._data.copy()

    def keys(self) -> t.KeysView[t.Hashable]:
        return self._data.keys()

    def values(self) -> t.ValuesView[t.Any]:
        return self._data.values()

    def items(self) -> t.ItemsView[t.Hashable, t.Any]:
        return self._data.items()

    def get_str(self, key: t.Hashable, default: str = "") -> str:
        value = self.get(key, default)
        return str(value) if value is not None else default

    def get_int(self, key: t.Hashable, default: int = 0) -> int:
        value = self.get(key, default)
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: t.Hashable, default: bool = False) -> bool:
        value = self.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "on")
        return bool(value)

    def get_type(self, key: t.Hashable, type_: type[t.Any], default: t.Any = None) -> t.Any:
        value = self.get(key, default)
        if isinstance(value, type_):
            return value
        try:
            return type_(value)
        except (ValueError, TypeError):
            return default
