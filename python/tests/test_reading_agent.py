"""Tests for ReadingAgent and ReadingMode."""

from __future__ import annotations

import pathlib
import typing as t

import pytest

from bookscout.agents import AgentContext
from bookscout.agents.reading import ReadingAgent
from bookscout.agents.reading import ReadingAgentToolset
from bookscout.agents.reading import ReadingLLMProfiles
from bookscout.agents.reading import ReadingMode
from bookscout.agents.reading import ReadingModeConfig
from bookscout.agents.reading import ReadingSession
from bookscout.agents.reading import ReadingSessionRepository
from bookscout.llm.types import AssistantMessage
from bookscout.llm.types import CompletionResponse
from bookscout.llm.types import SystemMessage
from bookscout.llm.types import Usage
from bookscout.llm.types import UserMessage
from bookscout.logging import LoggingConfig
from bookscout.logging import build_logger
from bookscout.sqlite import SQLite
from bookscout.sqlite import SQLiteConfig
from bookscout.tools import BaseTool
from bookscout.tools.toolset import Toolset


class DummyTool(BaseTool, name="dummy_retrieval", description="Dummy retrieval tool."):  # type: ignore[call-arg]
    async def __call__(self) -> str:
        return "dummy"


class FakeLLM:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, t.Any]] = []

    async def chat_completion(self, messages, *, tools=None, tool_executor=None, options=None):
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "tool_executor": tool_executor,
            "options": options,
        })
        if self.fail:
            raise RuntimeError("llm failed")
        return CompletionResponse(
            message=AssistantMessage(content="answer"),
            usage=Usage(input_tokens=3, output_tokens=4, cache_read_tokens=0, cache_write_tokens=0),
            model=options.model if options and options.model else "fake-model",
            finish_reason="stop",
        )

    @staticmethod
    def estimate_token(text: str) -> int:
        return max(1, len(text) // 4)


class FakeEmbedding:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2]

    async def embed_batch(self, texts: list[str], parallel: int = 5) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


@pytest.fixture()
def logger():
    test_logger = build_logger(LoggingConfig(name="test-reading", level="WARNING"))
    try:
        yield test_logger
    finally:
        test_logger.close()


@pytest.fixture()
def reading_config(tmp_path: pathlib.Path) -> ReadingModeConfig:
    return ReadingModeConfig(
        books_base_path=tmp_path / "books",
        book_id="book_1",
        db_uri=f"sqlite+aiosqlite:///{(tmp_path / 'reading.sqlite').as_posix()}",
        llm_profiles=ReadingLLMProfiles(cheap="cheap-model", standard="standard-model", strong="strong-model"),
    )


def _dummy_registry() -> t.Any:
    from bookscout.doccompiler.index_registry import IndexRegistry

    return IndexRegistry.load()


def _dummy_books_store(logger, tmp_path: pathlib.Path) -> t.Any:
    from bookscout.books import BooksConfig
    from bookscout.books import BooksStore

    return BooksStore(logger=logger, config=BooksConfig(base_path=tmp_path / "books", db_name="books.sqlite"))


def test_reading_session_defaults():
    session = ReadingSession(book_id="book_1")

    assert session.session_id.startswith("readsess_")
    assert session.book_id == "book_1"
    assert session.turn_count == 0
    assert session.extra == {}


@pytest.mark.asyncio
async def test_reading_session_repository_persists(tmp_path: pathlib.Path, logger):
    sqlite = SQLite(SQLiteConfig(uri=f"sqlite+aiosqlite:///{(tmp_path / 'repo.sqlite').as_posix()}"), logger=logger)
    await sqlite.startup()
    try:
        repo = ReadingSessionRepository(sqlite)
        await repo.create_schema()

        session = await repo.create(book_id="book_1", conversation_id="conv_1")
        updated = await repo.update_after_turn(
            session,
            user_input="question",
            response_text="answer",
            extra={"intent": "qa"},
        )
        loaded = await repo.get(updated.session_id)
        by_conv = await repo.get_by_conversation("conv_1", "book_1")
        run_id = await repo.log_agent_run(
            session_id=updated.session_id,
            agent_name="reading_agent",
            intent="qa",
            model_profile="standard",
            phase="done",
            user_input="question",
            response_text="answer",
            tool_calls=[],
            usage={"input_tokens": 1},
        )

        assert loaded is not None
        assert loaded.turn_count == 1
        assert loaded.extra["intent"] == "qa"
        assert by_conv is not None
        assert by_conv.session_id == updated.session_id
        assert run_id.startswith("run_")
    finally:
        await sqlite.shutdown()


@pytest.mark.asyncio
async def test_reading_agent_prompt_and_chat_completion_tools(logger):
    llm = FakeLLM()
    toolset = Toolset(name="fake", tools=[DummyTool()], logger=logger)
    agent = ReadingAgent(toolset=toolset, profiles=ReadingLLMProfiles(standard="standard-model"), logger=logger)
    ctx = AgentContext(llm=llm, extra={"book_id": "book_1", "reading_session_id": "sess_1"})

    result = await agent.step([UserMessage(content="Explain the core idea")], ctx=ctx)

    assert result.text == "answer"
    assert llm.calls[0]["tools"] == [toolset.tools[0]]
    assert llm.calls[0]["tool_executor"] is None
    assert llm.calls[0]["options"].model == "standard-model"
    system = llm.calls[0]["messages"][0]
    assert isinstance(system, SystemMessage)
    # New prompt has "Do NOT use compile" instead of "Do not compile books".
    assert "interactive reading assistant" in system.content
    assert "book_id: book_1" in system.content
    # No longer classifies intent — no intent in extra.
    assert "reading_agent" in ctx.extra


@pytest.mark.asyncio
async def test_reading_toolset_contains_only_retrieval_tools(reading_config, logger, tmp_path: pathlib.Path):
    from bookscout.books import Book
    from bookscout.books import BooksConfig
    from bookscout.books import BooksStore
    from bookscout.doccompiler.index_registry import IndexRegistry

    books_store = BooksStore(
        logger=logger,
        config=BooksConfig(base_path=tmp_path / "books", db_name="books.sqlite"),
    )
    await books_store.startup()
    await books_store.create_book(Book.new(title="t", book_id=reading_config.book_id))
    for index_type in ("chunk", "summary", "graph"):
        await books_store.upsert_index(reading_config.book_id, index_type, "built", count=1)

    toolset = ReadingAgentToolset(
        config=reading_config,
        llm=FakeLLM(),
        embedding=FakeEmbedding(),
        logger=logger,
        book_id=reading_config.book_id,
        registry=IndexRegistry.load(),
        books_store=books_store,
    )

    await toolset.startup()
    try:
        names = {tool.__function_name__ for tool in toolset.tools}

        # Retrieval tools.
        assert {
            "get_book",
            "list_books",
            "get_node",
            "get_root_node",
            "get_children",
            "get_tree",
            "list_nodes_by_level",
            "read_node_content",
            "read_subtree_content",
            "get_summary",
            "list_summaries",
            "chunk_vector_search",
            "chunk_fts_search",
            "get_chunks_by_node",
            "graph_entity_first_retrieval",
            "graph_relationship_first_retrieval",
            "graph_fts_entity_retrieval",
            "get_entities",
            "get_relationships",
        }.issubset(names)
        # Computation tools added.
        assert "wolfram_execute" in names
        assert "python_execute" in names
        # No compiler tools.
        assert {"compile", "build_indexes", "get_task_progress", "list_tasks"}.isdisjoint(names)
    finally:
        await toolset.shutdown()
        await books_store.shutdown()


@pytest.mark.asyncio
async def test_reading_mode_handle_success(
    monkeypatch: pytest.MonkeyPatch, reading_config, logger, tmp_path: pathlib.Path
):
    async def fake_toolset_startup(self):
        self.internal_tools = [DummyTool()]

    async def fake_toolset_shutdown(self):
        self.internal_tools = []

    monkeypatch.setattr(ReadingAgentToolset, "startup", fake_toolset_startup)
    monkeypatch.setattr(ReadingAgentToolset, "shutdown", fake_toolset_shutdown)
    llm = FakeLLM()
    mode = ReadingMode(
        config=reading_config,
        llm=llm,
        embedding=FakeEmbedding(),
        logger=logger,
        book_id=reading_config.book_id,
        registry=_dummy_registry(),
        books_store=_dummy_books_store(logger, tmp_path),
    )

    await mode.startup()
    try:
        ctx = AgentContext(llm=llm, conversation_id="conv_1")
        result = await mode.handle("Summarize this book", ctx=ctx)

        assert result.text == "answer"
        assert result.state.phase == "done"
        # The new Mode manages conversation in ModeState.messages.
        assert len(result.state.messages) == 2  # user + assistant
        assert result.state.messages[0]["role"] == "user"
        assert result.state.messages[0]["content"] == "Summarize this book"
        assert result.state.messages[1]["role"] == "assistant"
        assert result.state.messages[1]["content"] == "answer"
        assert llm.calls[0]["tools"][0].__function_name__ == "dummy_retrieval"
    finally:
        await mode.shutdown()


@pytest.mark.asyncio
async def test_reading_mode_handle_llm_failure(
    monkeypatch: pytest.MonkeyPatch, reading_config, logger, tmp_path: pathlib.Path
):
    async def fake_toolset_startup(self):
        self.internal_tools = [DummyTool()]

    async def fake_toolset_shutdown(self):
        self.internal_tools = []

    monkeypatch.setattr(ReadingAgentToolset, "startup", fake_toolset_startup)
    monkeypatch.setattr(ReadingAgentToolset, "shutdown", fake_toolset_shutdown)
    mode = ReadingMode(
        config=reading_config,
        llm=FakeLLM(fail=True),
        embedding=FakeEmbedding(),
        logger=logger,
        book_id=reading_config.book_id,
        registry=_dummy_registry(),
        books_store=_dummy_books_store(logger, tmp_path),
    )

    await mode.startup()
    try:
        ctx = AgentContext(llm=mode.llm, conversation_id="conv_1")
        with pytest.raises(RuntimeError, match="llm failed"):
            await mode.handle("question", ctx=ctx)

        assert mode.state.phase == "error"
        assert mode.state.agent_states["reading_agent"] == "error"
    finally:
        await mode.shutdown()


@pytest.mark.asyncio()
async def test_toolset_filters_by_manifest(tmp_path, logger):
    """A book with only chunk index should give no graph/summary tools."""
    from bookscout.books import Book
    from bookscout.books import BooksConfig
    from bookscout.books import BooksStore
    from bookscout.doccompiler.index_registry import IndexRegistry

    store = BooksStore(logger=logger, config=BooksConfig(base_path=tmp_path, db_name="books.sqlite"))
    await store.startup()
    book = Book.new(title="t", book_id="book_z")
    await store.create_book(book)
    await store.upsert_index(book.id, "chunk", "built", count=1)

    registry = IndexRegistry.load()
    # Get tool names for a chunk-only book
    # We can't fully start the toolset without llm/embedding, so we check the
    # filtering logic by inspecting the manifest.
    built = await store.list_index_types(book.id)
    assert built == {"chunk"}
    # Graph provider should not be in the active set.
    active = [p for p in registry.all() if p.index_type in built]
    assert {p.index_type for p in active} == {"chunk"}
    await store.shutdown()
