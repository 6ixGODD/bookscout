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


@pytest.mark.asyncio
async def test_append_and_load_messages(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="Chat")
    # Initially empty.
    assert await session_manager.load_messages(sess.session_id) == []

    # Append a user + assistant turn.
    await session_manager.append_message(sess.session_id, role="user", content="hello")
    await session_manager.append_message(sess.session_id, role="assistant", content="hi there")

    msgs = await session_manager.load_messages(sess.session_id)
    assert len(msgs) == 2
    assert msgs[0] == {"role": "user", "content": "hello"}
    assert msgs[1] == {"role": "assistant", "content": "hi there"}

    # Second turn.
    await session_manager.append_message(sess.session_id, role="user", content="how are you?")
    await session_manager.append_message(sess.session_id, role="assistant", content="fine!")

    msgs = await session_manager.load_messages(sess.session_id)
    assert len(msgs) == 4
    assert msgs[2]["role"] == "user"
    assert msgs[3]["content"] == "fine!"


@pytest.mark.asyncio
async def test_load_messages_isolation(session_manager: SessionManager):
    """Messages from one session don't leak into another."""
    s1 = await session_manager.create(book_id="book_1", name="S1")
    s2 = await session_manager.create(book_id="book_1", name="S2")
    await session_manager.append_message(s1.session_id, role="user", content="for s1")
    await session_manager.append_message(s2.session_id, role="user", content="for s2")

    m1 = await session_manager.load_messages(s1.session_id)
    m2 = await session_manager.load_messages(s2.session_id)
    assert len(m1) == 1
    assert m1[0]["content"] == "for s1"
    assert len(m2) == 1
    assert m2[0]["content"] == "for s2"


@pytest.mark.asyncio
async def test_delete(session_manager: SessionManager):
    sess = await session_manager.create(book_id="book_1", name="ToDelete")
    # Add some messages.
    await session_manager.append_message(sess.session_id, role="user", content="hello")
    await session_manager.append_message(sess.session_id, role="assistant", content="hi")
    # Delete.
    await session_manager.delete(sess.session_id)
    # Session is gone.
    loaded = await session_manager.get(sess.session_id)
    assert loaded is None
    # Messages are gone.
    msgs = await session_manager.load_messages(sess.session_id)
    assert msgs == []
