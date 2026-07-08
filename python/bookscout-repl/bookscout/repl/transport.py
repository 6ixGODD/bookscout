"""Transport abstraction — serializes events between REPL and external clients.

The REPL uses a :class:`Transport` to send/receive messages. The default
implementation (:class:`StdioTransport`) uses newline-delimited JSON over
stdin/stdout with 4-byte length-prefixed framing for binary safety.

Each message is a JSON object with a ``type`` field. This keeps the
protocol simple without requiring protobuf compilation at the TUI level.
"""

from __future__ import annotations

import abc
import asyncio
import json
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class Transport(LoggingMixin, AsyncResourceMixin, abc.ABC):
    """Abstract transport for REPL communication.

    Args:
        logger: Logger instance.
    """

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger=logger)

    @abc.abstractmethod
    async def send(self, message: dict[str, t.Any]) -> None:
        """Send a message to the client.

        Args:
            message: A JSON-serializable dict with a ``type`` field.
        """

    @abc.abstractmethod
    async def receive(self) -> dict[str, t.Any] | None:
        """Receive a message from the client.

        Returns:
            A dict, or ``None`` if the client has disconnected (EOF).
        """


class StdioTransport(Transport):
    """Stdio-based transport using 4-byte length-prefixed JSON.

    Each message is framed as: ``[4 bytes big-endian length][JSON bytes]``
    Written to stdout (send) and read from stdin (receive).

    This framing ensures binary safety and avoids newline-in-JSON issues.
    """

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger=logger)

    async def send(self, message: dict[str, t.Any]) -> None:
        """Send a message via stdout with length-prefix framing."""
        data = json.dumps(message, ensure_ascii=False).encode("utf-8")
        length = len(data).to_bytes(4, "big")
        # asyncio to_stdout doesn't exist — use sys.stdout.buffer directly.
        import sys

        sys.stdout.buffer.write(length + data)
        sys.stdout.buffer.flush()

    async def receive(self) -> dict[str, t.Any] | None:
        """Receive a message from stdin with length-prefix framing."""
        loop = asyncio.get_event_loop()

        def _read_exact(n: int) -> bytes | None:
            import sys

            buf = b""
            while len(buf) < n:
                chunk = sys.stdin.buffer.read(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return buf

        # Read 4-byte length prefix.
        length_bytes = await loop.run_in_executor(None, _read_exact, 4)
        if length_bytes is None:
            return None

        length = int.from_bytes(length_bytes, "big")
        if length <= 0 or length > 10 * 1024 * 1024:  # 10MB max
            self.logger.warning("invalid message length", length=length)
            return None

        # Read JSON body.
        body = await loop.run_in_executor(None, _read_exact, length)
        if body is None:
            return None

        return json.loads(body.decode("utf-8"))


__all__ = [
    "StdioTransport",
    "Transport",
]
