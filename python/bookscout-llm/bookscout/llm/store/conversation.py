"""Conversation and message persistence via SQLite.

Provides :class:`ConversationStore` for CRUD operations on conversations
and their messages, with automatic cleanup when limits are exceeded.
"""

from __future__ import annotations

import json
import typing as t

from sqlalchemy import func
from sqlmodel import Field as _Field
from sqlmodel import SQLModel as _SQLModel
from sqlmodel import col
from sqlmodel import select

from bookscout.core.lib.utils import gen_id
from bookscout.core.lib.utils import utcnow_ts
from bookscout.llm.config import ContextBudgetConfig
from bookscout.llm.types import AssistantMessage
from bookscout.llm.types import Message
from bookscout.llm.types import SystemMessage
from bookscout.llm.types import ToolResultMessage
from bookscout.llm.types import UserMessage
from bookscout.logging.mixin import LoggingMixin
from bookscout.sqlite import SQLite

if t.TYPE_CHECKING:
    from bookscout.logging import Logger


class ConversationRow(_SQLModel, table=True):
    """Index row for a conversation."""

    __tablename__ = "llm_conversation"

    conversation_id: str = _Field(primary_key=True)
    title: str | None = _Field(default=None)
    model: str = _Field(default="")
    created_at: float = _Field(default_factory=utcnow_ts)
    updated_at: float = _Field(default_factory=utcnow_ts)
    message_count: int = _Field(default=0)


class MessageRow(_SQLModel, table=True):
    """Index row for a message within a conversation."""

    __tablename__ = "llm_message"

    message_id: str = _Field(primary_key=True)
    conversation_id: str = _Field(index=True)
    role: str = _Field(default="user")
    content: str = _Field(default="")  # JSON-serialised message
    created_at: float = _Field(default_factory=utcnow_ts)


class ConversationStore(LoggingMixin):
    """CRUD for conversations and messages in SQLite.

    Args:
        logger: Logger instance.
        sqlite: Initialized :class:`SQLite` instance.
        budget_config: Context budget configuration for cleanup limits.
    """

    def __init__(self, logger: Logger, sqlite: SQLite, budget_config: ContextBudgetConfig) -> None:
        super().__init__(logger=logger)
        self._sqlite = sqlite
        self._budget = budget_config

    async def startup(self) -> None:
        """Create tables if they do not exist."""
        await self._sqlite.create_all([ConversationRow, MessageRow])
        await self._cleanup_old_conversations()
        self.logger.info("ConversationStore started")

    async def shutdown(self) -> None:
        """No-op — the SQLite engine is owned by the parent ChatModel."""
        self.logger.info("ConversationStore stopped")

    async def create(self, model: str, title: str | None = None) -> str:
        """Create a new conversation and return its ID."""
        conversation_id = gen_id(prefix="conv_")
        row = ConversationRow(
            conversation_id=conversation_id,
            title=title,
            model=model,
        )
        async with self._sqlite.session() as session:
            session.add(row)
            await session.commit()
        self.logger.info("Created conversation", conversation_id=conversation_id)
        return conversation_id  # type: ignore[no-any-return]

    async def get(self, conversation_id: str) -> ConversationRow | None:
        """Get a conversation by ID."""
        async with self._sqlite.session() as session:
            return t.cast("ConversationRow | None", await session.get(ConversationRow, conversation_id))

    async def list_conversations(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationRow]:
        """List conversations ordered by most recently updated."""
        async with self._sqlite.session() as session:
            stmt = select(ConversationRow).order_by(col(ConversationRow.updated_at).desc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete(self, conversation_id: str) -> None:
        """Delete a conversation and all its messages."""
        async with self._sqlite.session() as session:
            # Delete messages first
            stmt = select(MessageRow).where(MessageRow.conversation_id == conversation_id)
            result = await session.execute(stmt)
            for row in result.scalars().all():
                await session.delete(row)
            # Delete conversation
            conv = await session.get(ConversationRow, conversation_id)
            if conv is not None:
                await session.delete(conv)
            await session.commit()
        self.logger.info("Deleted conversation", conversation_id=conversation_id)

    async def add_message(self, conversation_id: str, message: Message) -> str:
        """Add a message to a conversation and return its ID."""
        message_id = gen_id(prefix="msg_")
        content_json = message.model_dump_json()

        async with self._sqlite.session() as session:
            conv = await session.get(ConversationRow, conversation_id)
            if conv is None:
                from ..exceptions import ConversationNotFoundError

                raise ConversationNotFoundError(conversation_id)

            row = MessageRow(
                message_id=message_id,
                conversation_id=conversation_id,
                role=message.role,
                content=content_json,
            )
            session.add(row)

            conv.message_count += 1
            conv.updated_at = utcnow_ts()
            session.add(conv)
            await session.commit()

        self.logger.debug(
            "Added message",
            conversation_id=conversation_id,
            message_id=message_id,
            role=message.role,
        )

        # Cleanup if over message limit
        await self._cleanup_long_conversation(conversation_id)
        return message_id  # type: ignore[no-any-return]

    async def get_messages(self, conversation_id: str) -> list[Message]:
        """Get all messages for a conversation, ordered by creation time."""
        async with self._sqlite.session() as session:
            stmt = (
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(col(MessageRow.created_at).asc())  # pylint: disable=no-member
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

        messages: list[Message] = []
        for row in rows:
            msg = _deserialize_message(row.content)
            if msg is not None:
                messages.append(msg)
        return messages

    async def get_message_rows(self, conversation_id: str) -> list[MessageRow]:
        """Get raw message rows for a conversation (for truncation)."""
        async with self._sqlite.session() as session:
            stmt = (
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(col(MessageRow.created_at).asc())  # pylint: disable=no-member
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def delete_message(self, message_id: str) -> None:
        """Delete a single message by ID."""
        async with self._sqlite.session() as session:
            row = await session.get(MessageRow, message_id)
            if row is None:
                return
            conversation_id = row.conversation_id
            await session.delete(row)

            # Decrement message count
            conv = await session.get(ConversationRow, conversation_id)
            if conv is not None:
                conv.message_count = max(0, conv.message_count - 1)
                conv.updated_at = utcnow_ts()
                session.add(conv)
            await session.commit()

    async def _cleanup_old_conversations(self) -> None:
        """Delete oldest conversations exceeding the configured limit."""
        max_convs = self._budget.max_conversations
        async with self._sqlite.session() as session:
            count_stmt = select(func.count()).select_from(ConversationRow)  # pylint: disable=not-callable
            count_result = await session.execute(count_stmt)
            total = int(count_result.scalar_one())

            if total <= max_convs:
                return

            excess = total - max_convs
            stmt = select(ConversationRow.conversation_id).order_by(col(ConversationRow.updated_at).asc()).limit(excess)  # pylint: disable=no-member
            result = await session.execute(stmt)
            old_ids = [row[0] for row in result.all()]

            for conv_id in old_ids:
                # Delete messages
                msg_stmt = select(MessageRow).where(MessageRow.conversation_id == conv_id)
                msg_result = await session.execute(msg_stmt)
                for msg_row in msg_result.scalars().all():
                    await session.delete(msg_row)
                # Delete conversation
                conv = await session.get(ConversationRow, conv_id)
                if conv is not None:
                    await session.delete(conv)

            await session.commit()
            self.logger.info("Cleaned up old conversations", removed_count=excess)

    async def _cleanup_long_conversation(self, conversation_id: str) -> None:
        """Prune oldest non-system messages if conversation exceeds message limit.

        System messages are always preserved; only user/assistant/tool messages
        are candidates for pruning.
        """
        max_msgs = self._budget.max_messages_per_conversation
        async with self._sqlite.session() as session:
            conv = await session.get(ConversationRow, conversation_id)
            if conv is None or conv.message_count <= max_msgs:
                return

            # Get non-system messages ordered oldest first
            stmt = (
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id, MessageRow.role != "system")
                .order_by(col(MessageRow.created_at).asc())  # pylint: disable=no-member
            )
            result = await session.execute(stmt)
            non_system = list(result.scalars().all())

            # Number of non-system messages to remove so total <= max_msgs.
            excess = conv.message_count - max_msgs
            if excess <= 0:
                return

            # Cap at the available non-system messages (defensive).
            excess = min(excess, len(non_system))
            for row in non_system[:excess]:
                await session.delete(row)
                conv.message_count -= 1

            conv.updated_at = utcnow_ts()
            session.add(conv)
            await session.commit()
            self.logger.info(
                "Pruned old messages",
                conversation_id=conversation_id,
                removed_count=excess,
            )


def _deserialize_message(content_json: str) -> Message | None:
    """Deserialize a JSON string back to a Message union type."""
    try:
        data = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return None

    role = data.get("role")
    if role == "system":
        return SystemMessage.model_validate(data)
    if role == "user":
        return UserMessage.model_validate(data)
    if role == "assistant":
        return AssistantMessage.model_validate(data)
    if role == "tool":
        return ToolResultMessage.model_validate(data)
    return None
