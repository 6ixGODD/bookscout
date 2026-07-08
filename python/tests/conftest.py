"""Shared pytest fixtures for the bookscout workspace tests."""

from __future__ import annotations

import pytest

from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger


@pytest.fixture()
def logger():
    """A quiet logger suitable for tests."""
    test_logger = build_logger(LoggingConfig(name="test", level="WARNING"))
    try:
        yield test_logger
    finally:
        test_logger.close()
