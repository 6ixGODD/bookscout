from __future__ import annotations

import pathlib
import tempfile

import pytest

from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger
from bookscout.repl.session_manager import SessionManager


@pytest.fixture
async def session_manager():
    with tempfile.TemporaryDirectory() as tmp:
        logger = build_logger(LoggingConfig(name="test", level="ERROR", targets=[]))
        mgr = SessionManager(workdir=pathlib.Path(tmp), logger=logger)
        await mgr.startup()
        yield mgr
        await mgr.shutdown()


@pytest.mark.asyncio
async def test_create_and_get(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="My Session", kind="chat")
    assert sess.book_id == "book_1"
    assert sess.name == "My Session"

    loaded = await session_manager.get(sess.session_id)
    assert loaded is not None
    assert loaded.name == "My Session"


@pytest.mark.asyncio
async def test_list_by_book(session_manager: SessionManager):
    await session_manager.create(book_id="book_1", name="A")
    await session_manager.create(book_id="book_1", name="B")
    await session_manager.create(book_id="book_2", name="C")

    b1 = await session_manager.list_by_book("book_1")
    assert len(b1) == 2

    all_s = await session_manager.list_all()
    assert len(all_s) == 3


@pytest.mark.asyncio
async def test_rename(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="Old")
    await session_manager.rename(sess.session_id, "New")
    loaded = await session_manager.get(sess.session_id)
    assert loaded.name == "New"


@pytest.mark.asyncio
async def test_update_after_turn(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="Test")
    await session_manager.update_after_turn(sess.session_id, user_input="hello", response_text="hi there")
    loaded = await session_manager.get(sess.session_id)
    assert loaded.turn_count == 1


@pytest.mark.asyncio
async def test_archive(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="ToArchive")
    await session_manager.archive(sess.session_id)
    loaded = await session_manager.get(sess.session_id)
    assert loaded.status == "archived"
    active = await session_manager.list_by_book("book_1")
    assert len(active) == 0
