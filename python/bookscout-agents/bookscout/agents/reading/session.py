"""Reading session persistence."""

from __future__ import annotations

import json
import typing as t

import pydantic

from bookscout.core.lib.utils import gen_id
from bookscout.core.lib.utils import utcnow_ts

if t.TYPE_CHECKING:
    from bookscout.sqlite import SQLite


class ReadingSession(pydantic.BaseModel):
    """Mutable reading session state persisted by :class:`ReadingMode`."""

    session_id: str = pydantic.Field(default_factory=lambda: gen_id(prefix="readsess_"))
    book_id: str
    conversation_id: str | None = None
    created_at: float = pydantic.Field(default_factory=utcnow_ts)
    updated_at: float = pydantic.Field(default_factory=utcnow_ts)
    turn_count: int = 0
    last_user_input: str = ""
    last_agent_response: str = ""
    extra: dict[str, t.Any] = pydantic.Field(default_factory=dict)


class ReadingSessionRepository:
    """SQLite repository for reading sessions and agent run logs."""

    def __init__(self, sqlite: SQLite) -> None:
        self._sqlite = sqlite

    async def create_schema(self) -> None:
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS reading_session (
                session_id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                conversation_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                turn_count INTEGER NOT NULL DEFAULT 0,
                last_user_input TEXT NOT NULL DEFAULT '',
                last_agent_response TEXT NOT NULL DEFAULT '',
                extra_json TEXT NOT NULL DEFAULT '{}'
            )""",
            readonly=False,
        )
        await self._sqlite.exec(
            """CREATE TABLE IF NOT EXISTS agent_run_log (
                run_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                intent TEXT NOT NULL,
                model_profile TEXT NOT NULL,
                phase TEXT NOT NULL,
                user_input TEXT NOT NULL,
                response_text TEXT NOT NULL DEFAULT '',
                tool_calls_json TEXT NOT NULL DEFAULT '[]',
                usage_json TEXT NOT NULL DEFAULT '{}',
                extra_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            )""",
            readonly=False,
        )

    async def get(self, session_id: str) -> ReadingSession | None:
        result = await self._sqlite.exec(
            "SELECT * FROM reading_session WHERE session_id = :session_id",
            readonly=True,
            session_id=session_id,
        )
        row = result.fetchone()
        return self._row_to_session(row) if row is not None else None

    async def get_by_conversation(self, conversation_id: str, book_id: str) -> ReadingSession | None:
        result = await self._sqlite.exec(
            """SELECT * FROM reading_session
               WHERE conversation_id = :conversation_id AND book_id = :book_id
               ORDER BY updated_at DESC LIMIT 1""",
            readonly=True,
            conversation_id=conversation_id,
            book_id=book_id,
        )
        row = result.fetchone()
        return self._row_to_session(row) if row is not None else None

    async def create(self, *, book_id: str, conversation_id: str | None = None) -> ReadingSession:
        session = ReadingSession(book_id=book_id, conversation_id=conversation_id)
        await self.save(session)
        return session

    async def save(self, session: ReadingSession) -> None:
        await self._sqlite.exec(
            """INSERT OR REPLACE INTO reading_session (
                session_id, book_id, conversation_id, created_at, updated_at,
                turn_count, last_user_input, last_agent_response, extra_json
            ) VALUES (
                :session_id, :book_id, :conversation_id, :created_at, :updated_at,
                :turn_count, :last_user_input, :last_agent_response, :extra_json
            )""",
            readonly=False,
            session_id=session.session_id,
            book_id=session.book_id,
            conversation_id=session.conversation_id,
            created_at=session.created_at,
            updated_at=session.updated_at,
            turn_count=session.turn_count,
            last_user_input=session.last_user_input,
            last_agent_response=session.last_agent_response,
            extra_json=json.dumps(session.extra),
        )

    async def update_after_turn(
        self,
        session: ReadingSession,
        *,
        user_input: str,
        response_text: str,
        extra: dict[str, t.Any] | None = None,
    ) -> ReadingSession:
        updated = session.model_copy(
            update={
                "updated_at": utcnow_ts(),
                "turn_count": session.turn_count + 1,
                "last_user_input": user_input,
                "last_agent_response": response_text,
                "extra": {**session.extra, **(extra or {})},
            }
        )
        await self.save(updated)
        return updated

    async def log_agent_run(
        self,
        *,
        session_id: str,
        agent_name: str,
        intent: str,
        model_profile: str,
        phase: str,
        user_input: str,
        response_text: str,
        tool_calls: list[dict[str, t.Any]],
        usage: dict[str, int],
        extra: dict[str, t.Any] | None = None,
    ) -> str:
        run_id = t.cast("str", gen_id(prefix="run_"))
        await self._sqlite.exec(
            """INSERT INTO agent_run_log (
                run_id, session_id, agent_name, intent, model_profile, phase,
                user_input, response_text, tool_calls_json, usage_json, extra_json, created_at
            ) VALUES (
                :run_id, :session_id, :agent_name, :intent, :model_profile, :phase,
                :user_input, :response_text, :tool_calls_json, :usage_json, :extra_json, :created_at
            )""",
            readonly=False,
            run_id=run_id,
            session_id=session_id,
            agent_name=agent_name,
            intent=intent,
            model_profile=model_profile,
            phase=phase,
            user_input=user_input,
            response_text=response_text,
            tool_calls_json=json.dumps(tool_calls),
            usage_json=json.dumps(usage),
            extra_json=json.dumps(extra or {}),
            created_at=utcnow_ts(),
        )
        return run_id

    @staticmethod
    def _row_to_session(row: t.Any) -> ReadingSession:
        mapping = row._mapping if hasattr(row, "_mapping") else row
        return ReadingSession(
            session_id=mapping["session_id"],
            book_id=mapping["book_id"],
            conversation_id=mapping["conversation_id"],
            created_at=mapping["created_at"],
            updated_at=mapping["updated_at"],
            turn_count=mapping["turn_count"],
            last_user_input=mapping["last_user_input"],
            last_agent_response=mapping["last_agent_response"],
            extra=json.loads(mapping["extra_json"] or "{}"),
        )
