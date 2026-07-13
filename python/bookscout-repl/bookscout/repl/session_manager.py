"""Global session manager — SQLite-backed session store at workdir/sessions.db."""

from __future__ import annotations

import json
import pathlib
import typing as t

import pydantic

from bookscout.core.lib.utils import gen_id
from bookscout.core.lib.utils import utcnow_ts
from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging import Logger
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig


class Session(pydantic.BaseModel):
    session_id: str = pydantic.Field(default_factory=lambda: gen_id(prefix="sess_"))
    book_id: str
    name: str  # default: "<book_title>-<random6>"
    kind: str = "chat"
    created_at: float = pydantic.Field(default_factory=utcnow_ts)
    updated_at: float = pydantic.Field(default_factory=utcnow_ts)
    turn_count: int = 0
    status: str = "active"  # 'active' | 'archived'
    extra: dict[str, t.Any] = pydantic.Field(default_factory=dict)


class SessionManager(LoggingMixin, AsyncResourceMixin):
    def __init__(self, workdir: pathlib.Path, logger: Logger) -> None:
        super().__init__(logger=logger)
        db_path = workdir / "sessions.db"
        self._sqlite = SQLite(
            config=SQLiteConfig(uri=f"sqlite+aiosqlite:///{db_path}"),
            logger=logger,
        )

    async def startup(self) -> None:
        await self._sqlite.startup()
        await self._create_schema()
        await super().startup()

    async def shutdown(self) -> None:
        await self._sqlite.shutdown()
        await super().shutdown()

    async def _create_schema(self) -> None:
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS session (
                session_id   TEXT PRIMARY KEY,
                book_id      TEXT NOT NULL,
                name         TEXT NOT NULL,
                kind         TEXT NOT NULL DEFAULT 'chat',
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL,
                turn_count   INTEGER NOT NULL DEFAULT 0,
                status       TEXT NOT NULL DEFAULT 'active',
                extra_json   TEXT NOT NULL DEFAULT '{}'
            )""",
            readonly=False,
        )
        await self._sqlite.exec(
            "CREATE INDEX IF NOT EXISTS idx_session_book ON session(book_id)",
            readonly=False,
        )
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS message_log (
                msg_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                role         TEXT NOT NULL,
                content      TEXT NOT NULL,
                created_at   REAL NOT NULL,
                FOREIGN KEY (session_id) REFERENCES session(session_id)
            )""",
            readonly=False,
        )
        await self._sqlite.exec(
            "CREATE INDEX IF NOT EXISTS idx_msg_session ON message_log(session_id)",
            readonly=False,
        )

    async def create(self, *, book_id: str, name: str, kind: str = "chat") -> Session:
        session = Session(book_id=book_id, name=name, kind=kind)
        await self._save(session)
        return session

    async def get(self, session_id: str) -> Session | None:
        result = await self._sqlite.exec(
            "SELECT * FROM session WHERE session_id = :sid",
            readonly=True,
            sid=session_id,
        )
        row = result.fetchone()
        return self._row_to_session(row) if row else None

    async def list_by_book(self, book_id: str) -> list[Session]:
        result = await self._sqlite.exec(
            "SELECT * FROM session WHERE book_id = :bid AND status = 'active' ORDER BY updated_at DESC",
            readonly=True,
            bid=book_id,
        )
        return [self._row_to_session(r) for r in result.fetchall()]

    async def list_all(self) -> list[Session]:
        result = await self._sqlite.exec(
            "SELECT * FROM session WHERE status = 'active' ORDER BY updated_at DESC",
            readonly=True,
        )
        return [self._row_to_session(r) for r in result.fetchall()]

    async def rename(self, session_id: str, name: str) -> None:
        await self._sqlite.exec(
            "UPDATE session SET name = :name, updated_at = :ts WHERE session_id = :sid",
            readonly=False,
            name=name,
            ts=utcnow_ts(),
            sid=session_id,
        )

    async def update_after_turn(self, session_id: str, *, user_input: str, response_text: str) -> None:
        await self._sqlite.exec(
            """UPDATE session SET
               updated_at = :ts, turn_count = turn_count + 1,
               extra_json = :extra
               WHERE session_id = :sid""",
            readonly=False,
            ts=utcnow_ts(),
            sid=session_id,
            extra=json.dumps({"last_user_input": user_input, "last_response": response_text[:200]}),
        )

    async def append_message(self, session_id: str, *, role: str, content: str) -> None:
        """Append a single message to the session's conversation log."""
        await self._sqlite.exec(
            """INSERT INTO message_log (session_id, role, content, created_at)
               VALUES (:sid, :role, :content, :ts)""",
            readonly=False,
            sid=session_id,
            role=role,
            content=content,
            ts=utcnow_ts(),
        )

    async def load_messages(self, session_id: str) -> list[dict[str, str]]:
        """Load the full conversation history for a session.

        Returns a list of ``{"role": "user"|"assistant", "content": str}``
        ordered chronologically.
        """
        result = await self._sqlite.exec(
            "SELECT role, content FROM message_log WHERE session_id = :sid ORDER BY msg_id ASC",
            readonly=True,
            sid=session_id,
        )
        return [{"role": row[0], "content": row[1]} for row in result.fetchall()]

    async def archive(self, session_id: str) -> None:
        await self._sqlite.exec(
            "UPDATE session SET status = 'archived', updated_at = :ts WHERE session_id = :sid",
            readonly=False,
            ts=utcnow_ts(),
            sid=session_id,
        )

    async def _save(self, session: Session) -> None:
        await self._sqlite.exec(
            """INSERT OR REPLACE INTO session (
                session_id, book_id, name, kind, created_at, updated_at,
                turn_count, status, extra_json
            ) VALUES (
                :sid, :bid, :name, :kind, :ca, :ua, :tc, :st, :ex
            )""",
            readonly=False,
            sid=session.session_id,
            bid=session.book_id,
            name=session.name,
            kind=session.kind,
            ca=session.created_at,
            ua=session.updated_at,
            tc=session.turn_count,
            st=session.status,
            ex=json.dumps(session.extra),
        )

    @staticmethod
    def _row_to_session(row: t.Any) -> Session:
        m = row._mapping if hasattr(row, "_mapping") else row
        return Session(
            session_id=m["session_id"],
            book_id=m["book_id"],
            name=m["name"],
            kind=m["kind"],
            created_at=m["created_at"],
            updated_at=m["updated_at"],
            turn_count=m["turn_count"],
            status=m["status"],
            extra=json.loads(m["extra_json"] or "{}"),
        )
