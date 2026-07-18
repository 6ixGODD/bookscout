"""Rate limiter for LLM API calls with SQLite-backed rolling windows.

Supports two modes:
  - ``"requests"`` — limits the number of API calls per window.
  - ``"tokens"``  — limits the total token consumption per window.

Token tracking is dual-track:
  1. On request, tiktoken estimates are recorded immediately.
  2. On response, actual usage (from the model's ``usage`` field) replaces
     the estimate for that request.

Rolling windows: 5 h, weekly (7 d), monthly (30 d).  All counters are
persisted in SQLite so they survive restarts — essential for the monthly
window.
"""

from __future__ import annotations

import time
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig

from .exceptions import RateLimitError

if t.TYPE_CHECKING:
    from bookscout.logging import Logger

    from .config import RateLimitConfig as _RateLimitConfig

# ---------------------------------------------------------------------------
# Window definitions (name, duration in seconds)
# ---------------------------------------------------------------------------

_WINDOWS: list[tuple[str, float]] = [
    ("rolling_5h", 5 * 3600),
    ("rolling_weekly", 7 * 86400),
    ("rolling_monthly", 30 * 86400),
]


class RateLimiter(LoggingMixin, AsyncResourceMixin):
    """SQLite-backed rate limiter with rolling windows.

    Usage::

        limiter = RateLimiter(config=cfg, logger=log)
        await limiter.startup()

        # Before an LLM call:
        await limiter.check_allowed(estimated_tokens=1200)

        # After the call returns:
        await limiter.record_actual_usage(input_tokens=1000, output_tokens=350)
    """

    def __init__(self, config: _RateLimitConfig, logger: Logger) -> None:
        super().__init__(logger=logger)
        self._config = config
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=config.db_uri),
            logger=logger,
        )

    # -- AsyncResourceMixin --------------------------------------------------

    async def startup(self) -> None:
        await self._sqlite.startup()
        await self._create_schema()

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()

    # -- Schema --------------------------------------------------------------

    async def _create_schema(self) -> None:
        await self._sqlite.exec(
            """
            CREATE TABLE IF NOT EXISTS rate_limit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL    NOT NULL,
                mode        TEXT    NOT NULL,
                estimated   INTEGER NOT NULL DEFAULT 0,
                actual_in   INTEGER,
                actual_out  INTEGER
            )
            """,
            readonly=False,
        )
        await self._sqlite.exec(
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_ts ON rate_limit_log (ts)",
            readonly=False,
        )

    # -- Public API ----------------------------------------------------------

    async def check_allowed(self, *, estimated_tokens: int = 0) -> None:
        """Raise :class:`RateLimitError` if any configured window is exceeded.

        Must be called *before* the LLM request is made.  In ``"requests"``
        mode the count of rows in the window is checked; in ``"tokens"`` mode
        the sum of token usage (actual where available, estimated otherwise)
        is checked.

        Args:
            estimated_tokens: tiktoken estimate for the upcoming request.
                Ignored in ``"requests"`` mode.
        """
        mode = self._config.mode
        if mode == "off":
            return

        now = time.time()
        for window_name, window_secs in _WINDOWS:
            limit = self._get_window_limit(window_name)
            if limit <= 0:
                continue  # 0 = unlimited

            cutoff = now - window_secs
            if mode == "requests":
                count = await self._count_requests(cutoff)
                if count >= limit:
                    raise RateLimitError(
                        f"Request limit exceeded for {window_name}: "
                        f"{count}/{limit} requests in the last "
                        f"{self._fmt_duration(window_secs)}"
                    )
            elif mode == "tokens":
                used = await self._sum_tokens(cutoff)
                # Include the pending request's estimate in the check.
                projected = used + estimated_tokens
                if projected > limit:
                    raise RateLimitError(
                        f"Token limit exceeded for {window_name}: "
                        f"{used}+{estimated_tokens}(est) > {limit} tokens in the last "
                        f"{self._fmt_duration(window_secs)}"
                    )

    async def record_request(self, *, estimated_tokens: int = 0) -> int:
        """Record that a request is about to be made.

        In ``"tokens"`` mode the tiktoken estimate is stored; it will be
        replaced by actual usage when :meth:`record_actual_usage` is called.

        Returns:
            The auto-generated row id (for later update with actual usage).
        """
        mode = self._config.mode
        if mode == "off":
            return 0

        now = time.time()
        result = await self._sqlite.exec(
            """
            INSERT INTO rate_limit_log (ts, mode, estimated, actual_in, actual_out)
            VALUES (:ts, :mode, :estimated, NULL, NULL)
            """,
            readonly=False,
            ts=now,
            mode=mode,
            estimated=estimated_tokens if mode == "tokens" else 0,
        )
        row_id = result.lastrowid  # type: ignore[union-attr]
        self.logger.debug(
            "rate_limit: request recorded",
            mode=mode,
            estimated_tokens=estimated_tokens,
            row_id=row_id,
        )
        return row_id or 0

    async def record_actual_usage(
        self,
        row_id: int,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Update a previously recorded request with actual token usage.

        If *row_id* is 0 (rate limiting off), this is a no-op.

        Args:
            row_id: The id returned by :meth:`record_request`.
            input_tokens: Actual input tokens from the model's usage field.
            output_tokens: Actual output tokens from the model's usage field.
        """
        if row_id == 0 or self._config.mode == "off":
            return

        await self._sqlite.exec(
            """
            UPDATE rate_limit_log
            SET actual_in = :actual_in, actual_out = :actual_out
            WHERE id = :row_id
            """,
            readonly=False,
            actual_in=input_tokens,
            actual_out=output_tokens,
            row_id=row_id,
        )
        self.logger.debug(
            "rate_limit: actual usage recorded",
            row_id=row_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def get_usage(self) -> dict[str, dict[str, t.Any]]:
        """Return current usage stats for all windows.

        Returns:
            A dict keyed by window name, each containing::

                {
                    "limit": int,          # configured limit (0 = unlimited)
                    "requests": int,       # request count in window
                    "tokens_used": int,    # token consumption in window
                    "window_secs": float,  # window duration in seconds
                }
        """
        now = time.time()
        result: dict[str, dict[str, t.Any]] = {}
        for window_name, window_secs in _WINDOWS:
            cutoff = now - window_secs
            limit = self._get_window_limit(window_name)
            count = await self._count_requests(cutoff)
            used = await self._sum_tokens(cutoff)
            result[window_name] = {
                "limit": limit,
                "requests": count,
                "tokens_used": used,
                "window_secs": window_secs,
            }
        return result

    # -- Internal helpers ----------------------------------------------------

    def _get_window_limit(self, window_name: str) -> int:
        """Return the configured limit for a window (0 = unlimited)."""
        windows = self._config.windows
        mapping = {
            "rolling_5h": windows.rolling_5h.limit,
            "rolling_weekly": windows.rolling_weekly.limit,
            "rolling_monthly": windows.rolling_monthly.limit,
        }
        return mapping.get(window_name, 0)

    async def _count_requests(self, cutoff: float) -> int:
        """Count requests in the log since *cutoff*."""
        result = await self._sqlite.exec(
            "SELECT COUNT(*) FROM rate_limit_log WHERE ts >= :cutoff",
            readonly=True,
            cutoff=cutoff,
        )
        row = result.fetchone()
        return row[0] if row else 0

    async def _sum_tokens(self, cutoff: float) -> int:
        """Sum token usage since *cutoff*.

        For each row, uses actual usage if available, otherwise the estimate.
        """
        result = await self._sqlite.exec(
            """
            SELECT COALESCE(
                SUM(
                    COALESCE(actual_in, 0) + COALESCE(actual_out, 0)
                ),
                0
            ) AS actual_total,
            COALESCE(
                SUM(
                    CASE
                        WHEN actual_in IS NULL THEN estimated
                        ELSE 0
                    END
                ),
                0
            ) AS estimated_remaining
            FROM rate_limit_log
            WHERE ts >= :cutoff
            """,
            readonly=True,
            cutoff=cutoff,
        )
        row = result.fetchone()
        if row is None:
            return 0
        actual_total = row[0] or 0
        estimated_remaining = row[1] or 0
        return actual_total + estimated_remaining

    @staticmethod
    def _fmt_duration(secs: float) -> str:
        """Format a duration in seconds to a human-readable string."""
        if secs >= 86400:
            days = int(secs // 86400)
            return f"{days}d"
        hours = int(secs // 3600)
        return f"{hours}h"
