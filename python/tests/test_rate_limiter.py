"""Tests for bookscout.llm.rate_limiter — SQLite-backed rolling-window rate limiter."""

from __future__ import annotations

import pytest

from bookscout.llm.config import RateLimitConfig
from bookscout.llm.config import _RLWindowConfig
from bookscout.llm.config import _RLWindowsConfig
from bookscout.llm.exceptions import RateLimitError
from bookscout.llm.rate_limiter import RateLimiter
from bookscout.logging import Logger
from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger


@pytest.fixture
def logger() -> Logger:
    return build_logger(LoggingConfig(name="test", level="ERROR", targets=[]))


@pytest.fixture
def config_off() -> RateLimitConfig:
    return RateLimitConfig(mode="off")


@pytest.fixture
def config_requests() -> RateLimitConfig:
    return RateLimitConfig(
        mode="requests",
        windows=_RLWindowsConfig(
            rolling_5h=_RLWindowConfig(limit=3),
            rolling_weekly=_RLWindowConfig(limit=0),
            rolling_monthly=_RLWindowConfig(limit=0),
        ),
        db_uri="sqlite+aiosqlite:///:memory:",
    )


@pytest.fixture
def config_tokens() -> RateLimitConfig:
    return RateLimitConfig(
        mode="tokens",
        windows=_RLWindowsConfig(
            rolling_5h=_RLWindowConfig(limit=100),
            rolling_weekly=_RLWindowConfig(limit=0),
            rolling_monthly=_RLWindowConfig(limit=0),
        ),
        db_uri="sqlite+aiosqlite:///:memory:",
    )


async def _make_limiter(config: RateLimitConfig, logger: Logger) -> RateLimiter:
    limiter = RateLimiter(config=config, logger=logger)
    await limiter.startup()
    return limiter


@pytest.mark.asyncio
async def test_off_mode_allows_everything(config_off: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_off, logger)
    try:
        await limiter.check_allowed(estimated_tokens=999999)
        # Should not raise, even with huge token count.
        row_id = await limiter.record_request(estimated_tokens=999999)
        assert row_id == 0  # off mode returns 0
        await limiter.record_actual_usage(row_id, input_tokens=999, output_tokens=999)
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_requests_mode_blocks_on_limit(config_requests: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_requests, logger)
    try:
        # Record 3 requests (limit is 3).
        for _ in range(3):
            await limiter.check_allowed()
            await limiter.record_request()

        # 4th should raise.
        with pytest.raises(RateLimitError, match="rolling_5h"):
            await limiter.check_allowed()
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_requests_mode_allows_under_limit(config_requests: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_requests, logger)
    try:
        # Record 2 requests (limit is 3).
        for _ in range(2):
            await limiter.check_allowed()
            await limiter.record_request()

        # 3rd should still be allowed.
        await limiter.check_allowed()
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_tokens_mode_blocks_on_limit(config_tokens: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_tokens, logger)
    try:
        # Record a request with 60 estimated tokens.
        await limiter.check_allowed(estimated_tokens=60)
        row_id = await limiter.record_request(estimated_tokens=60)
        # Update with actual: 50 in + 30 out = 80.
        await limiter.record_actual_usage(row_id, input_tokens=50, output_tokens=30)

        # Next request: 30 est → used=80+30=110 > 100 limit.
        with pytest.raises(RateLimitError, match="rolling_5h"):
            await limiter.check_allowed(estimated_tokens=30)
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_tokens_mode_allows_under_limit(config_tokens: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_tokens, logger)
    try:
        # Record a request with 30 estimated tokens.
        await limiter.check_allowed(estimated_tokens=30)
        row_id = await limiter.record_request(estimated_tokens=30)
        await limiter.record_actual_usage(row_id, input_tokens=20, output_tokens=10)

        # Next request: 40 est → used=30+40=70 < 100 limit.
        await limiter.check_allowed(estimated_tokens=40)
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_tokens_mode_uses_estimate_when_no_actual(config_tokens: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_tokens, logger)
    try:
        # Record a request with 70 estimated tokens, but don't update actual.
        await limiter.check_allowed(estimated_tokens=70)
        await limiter.record_request(estimated_tokens=70)

        # Next request: 40 est → used=70(estimated)+40=110 > 100 limit.
        with pytest.raises(RateLimitError, match="rolling_5h"):
            await limiter.check_allowed(estimated_tokens=40)
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_get_usage(config_requests: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_requests, logger)
    try:
        # Record 2 requests.
        await limiter.record_request()
        row_id = await limiter.record_request(estimated_tokens=50)
        await limiter.record_actual_usage(row_id, input_tokens=30, output_tokens=15)

        usage = await limiter.get_usage()
        assert "rolling_5h" in usage
        assert usage["rolling_5h"]["limit"] == 3
        assert usage["rolling_5h"]["requests"] == 2
        assert usage["rolling_5h"]["tokens_used"] == 45  # 30 + 15

        assert "rolling_weekly" in usage
        assert usage["rolling_weekly"]["limit"] == 0  # unlimited
        assert "rolling_monthly" in usage
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_unlimited_window_allows_anything(logger: Logger) -> None:
    config = RateLimitConfig(
        mode="requests",
        windows=_RLWindowsConfig(
            rolling_5h=_RLWindowConfig(limit=0),  # unlimited
            rolling_weekly=_RLWindowConfig(limit=0),
            rolling_monthly=_RLWindowConfig(limit=0),
        ),
        db_uri="sqlite+aiosqlite:///:memory:",
    )
    limiter = await _make_limiter(config, logger)
    try:
        for _ in range(100):
            await limiter.check_allowed()
            await limiter.record_request()
        # Should not raise.
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_record_actual_usage_noop_for_zero_row_id(config_off: RateLimitConfig, logger: Logger) -> None:
    limiter = await _make_limiter(config_off, logger)
    try:
        # Should not raise.
        await limiter.record_actual_usage(0, input_tokens=100, output_tokens=50)
    finally:
        await limiter.shutdown()


@pytest.mark.asyncio
async def test_fmt_duration() -> None:
    assert RateLimiter._fmt_duration(5 * 3600) == "5h"
    assert RateLimiter._fmt_duration(7 * 86400) == "7d"
    assert RateLimiter._fmt_duration(30 * 86400) == "30d"
