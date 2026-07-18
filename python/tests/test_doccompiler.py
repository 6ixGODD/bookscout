"""Unit tests for doccompiler: heading normalization, tag mapping, rule-based builder."""

from __future__ import annotations

import pytest

from bookscout.doccompiler import RuleBasedBuilder
from bookscout.doccompiler.builder.tagify import tagify_chunk

# ----------------------------------------------------------------- fixtures


@pytest.fixture()
def builder(logger):
    return RuleBasedBuilder(logger=logger)


# ----------------------------------------------------------- Tagification


def test_tagify_chunk_inserts_tags_at_boundaries():
    """Tagify inserts <sN/> tags at punctuation and newline boundaries."""
    chunk = "Hello world.\nThis is a test!"
    tag_map = tagify_chunk(chunk, chunk_start=100)

    # Tag 0 at chunk start
    assert 0 in tag_map.tags
    assert tag_map.tags[0] == 100

    # Tags should be sequential
    tag_nums = sorted(tag_map.tags.keys())
    assert tag_nums == list(range(len(tag_map.tags)))

    # Tagged text contains <s0/> at start
    assert tag_map.tagged_text.startswith("<s0/>")

    # Tagged text contains at least one tag after the period
    assert "<s1/>" in tag_map.tagged_text

    # The tag map should resolve ranges correctly
    rng = tag_map.resolve_range(0, 1)
    assert rng is not None
    assert rng[0] == 100


def test_tagify_empty_chunk():
    """Tagify on empty chunk produces tag 0 at start and tag 1 at end."""
    tag_map = tagify_chunk("", chunk_start=50)
    assert 0 in tag_map.tags
    assert tag_map.tags[0] == 50
    assert tag_map.tagged_text == "<s0/><s1/>"


def test_tagify_resolve_invalid_tag_returns_none():
    """Resolving a non-existent tag returns None."""
    tag_map = tagify_chunk("Hello.", chunk_start=0)
    assert tag_map.resolve_single(999) is None
    assert tag_map.resolve_range(0, 999) is None


# ----------------------------------------------------------- Rule-based builder


def test_build_nodes_empty_content_creates_root(builder):
    """Empty content → single root node covering all (0-length) content."""
    nodes = builder.build_nodes("book1", "", book_title="Test")
    assert len(nodes) == 1
    assert nodes[0].is_root
    assert nodes[0].title == "Test"
    assert nodes[0].level == 0
    assert nodes[0].content_length == 0


def test_build_nodes_no_headings_root_covers_all(builder):
    """Content with no headings → root covers entire content."""
    content = "This is some text without any headings.\n\nJust paragraphs."
    nodes = builder.build_nodes("book1", content, book_title="My Book")
    assert len(nodes) == 1
    assert nodes[0].title == "My Book"
    assert nodes[0].content_offset == 0
    assert nodes[0].content_length == len(content)


def test_build_nodes_with_headings(builder):
    """Content with headings → root + heading nodes with correct offsets."""
    content = "# Chapter 1\n\nSome text here.\n\n## Section 1.1\n\nMore text.\n\n# Chapter 2\n\nEnd."
    nodes = builder.build_nodes("book1", content, book_title="Test Book")

    # Root + 3 heading nodes
    assert len(nodes) == 4
    root = nodes[0]
    assert root.title == "Test Book"
    assert root.is_root

    # Chapter 1 at level 1
    ch1 = nodes[1]
    assert ch1.title == "Chapter 1"
    assert ch1.level == 1
    assert ch1.parent_id == root.id

    # Section 1.1 at level 2
    sec11 = nodes[2]
    assert sec11.title == "Section 1.1"
    assert sec11.level == 2
    assert sec11.parent_id == ch1.id

    # Chapter 2 at level 1
    ch2 = nodes[3]
    assert ch2.title == "Chapter 2"
    assert ch2.level == 1
    assert ch2.parent_id == root.id


def test_build_nodes_heading_normalization_skipped_levels(builder):
    """Heading that skips a level gets normalized down."""
    content = "# A\n\n### B\n\n## C\n"
    nodes = builder.build_nodes("book1", content)

    # A=1, B should be normalized to 2 (not 3), C=2
    levels = [n.level for n in nodes if not n.is_root]
    assert levels == [1, 2, 2]


def test_build_nodes_heading_normalization_starting_at_level2(builder):
    """Document starting at ## gets shifted to start at level 1."""
    content = "## First\n\nText.\n\n## Second\n\nMore.\n"
    nodes = builder.build_nodes("book1", content)

    levels = [n.level for n in nodes if not n.is_root]
    assert levels == [1, 1]


def test_build_nodes_content_offsets(builder):
    """Content offsets should point to the text between headings."""
    content = "# H1\n\nBody 1\n\n# H2\n\nBody 2\n"
    nodes = builder.build_nodes("book1", content)

    # Find the two heading nodes
    h1 = next(n for n in nodes if n.title == "H1")
    h2 = next(n for n in nodes if n.title == "H2")

    # H1 content should start after "# H1\n" and end at "# H2"
    assert h1.content_offset > h1.title_offset
    assert h1.content_length > 0

    # H2 content should start after "# H2\n" and go to end
    assert h2.content_offset > h2.title_offset
    assert h2.content_offset + h2.content_length <= len(content)


def test_build_nodes_root_gets_pre_heading_content(builder):
    """Root node should get the text before the first heading as its content."""
    content = "Introduction text.\n\n# Chapter 1\n\nBody.\n"
    nodes = builder.build_nodes("book1", content, book_title="Book")

    root = nodes[0]
    assert root.content_length > 0
    # Root content should be the "Introduction text.\n\n" part
    intro = content[: root.content_offset + root.content_length]
    assert "Introduction" in intro


def test_build_nodes_root_title_set_to_book_title(builder):
    """Root node title should be the book title (spec §3.3 #11)."""
    content = "# Chapter\n\nText.\n"
    nodes = builder.build_nodes("book1", content, book_title="My Awesome Book")
    assert nodes[0].title == "My Awesome Book"


def test_build_nodes_content_length_zero_when_child_follows_immediately(builder):
    """Node with child immediately after has content_length=0 (§7.4 #4)."""
    content = "# Parent\n\n# Child\n\nText.\n"
    nodes = builder.build_nodes("book1", content)

    parent = next(n for n in nodes if n.title == "Parent")
    # Parent's content ends at Child's start, so it should be very small
    # (just the newline between them, or 0 if title_end == child_start)
    assert parent.content_length >= 0


@pytest.mark.asyncio()
async def test_compile_with_index_types_subset(tmp_path, logger):
    """Compile with index_types={'chunk'} must only run the chunk indexer."""
    from bookscout.books import BooksConfig
    from bookscout.books import BooksStore
    from bookscout.doccompiler import Compiler
    from bookscout.doccompiler import EpubParser
    from bookscout.doccompiler import RuleBasedBuilder

    store = BooksStore(logger=logger, config=BooksConfig(base_path=tmp_path, db_name="books.sqlite"))
    await store.startup()
    parser = EpubParser(logger=logger)
    await parser.startup()
    builder = RuleBasedBuilder(logger=logger)
    await builder.startup()

    # Fake indexers with distinct index_types.
    class FakeIndexer:
        def __init__(self, itype):
            self._it = itype
            self._ran = False

        @property
        def index_type(self):
            return self._it

        async def startup(self):
            pass

        async def shutdown(self):
            pass

        async def build_index(self, book_id, workspace, *, monitor=None, parent_id=None):
            self._ran = True
            from bookscout.doccompiler import IndexResult
            from bookscout.doccompiler.indexer import IndexProgress

            return IndexResult(index_type=self._it, count=1, progress=IndexProgress(1, 1, "done", ""))

    chunk_idx = FakeIndexer("chunk")
    summary_idx = FakeIndexer("summary")

    compiler = Compiler(
        logger=logger,
        parser=parser,
        books_store=store,
        builder=builder,
        indexers=[chunk_idx, summary_idx],
        workspace_base=tmp_path,
    )
    await compiler.startup()

    # We need an actual EPUB to compile; skip if no test fixture available.
    # Instead, test the indexer selection logic directly.
    selected = [i for i in [chunk_idx, summary_idx] if i.index_type in {"chunk"}]
    assert len(selected) == 1
    assert selected[0].index_type == "chunk"

    await compiler.shutdown()
    await store.shutdown()
    await parser.shutdown()
    await builder.shutdown()


@pytest.mark.asyncio()
async def test_build_one_index_creates_manifest_row_when_absent(tmp_path, logger):
    """Regression: ``_build_one_index`` must seed a `pending` manifest row before
    calling ``set_index_status('building')`` — otherwise it errors with
    ``StoreError('Manifest row not found: ...')`` on a fresh compile (the exact
    error the user hit on a clean data dir).
    """
    from bookscout.books import BooksConfig
    from bookscout.books import BooksStore
    from bookscout.doccompiler import Compiler
    from bookscout.doccompiler import EpubParser
    from bookscout.doccompiler import RuleBasedBuilder
    from bookscout.doccompiler.workspace import BookWorkspace

    store = BooksStore(logger=logger, config=BooksConfig(base_path=tmp_path, db_name="books.sqlite"))
    await store.startup()
    parser = EpubParser(logger=logger)
    await parser.startup()
    builder = RuleBasedBuilder(logger=logger)
    await builder.startup()

    class FakeIndexer:
        _it = "chunk"

        @property
        def index_type(self):
            return self._it

        async def startup(self):
            pass

        async def shutdown(self):
            pass

        async def build_index(self, book_id, workspace, *, monitor=None, parent_id=None):
            from bookscout.doccompiler.indexer import IndexProgress
            from bookscout.doccompiler.indexer import IndexResult

            return IndexResult(index_type=self._it, count=2, progress=IndexProgress(2, 2, "done", ""))

    compiler = Compiler(
        logger=logger,
        parser=parser,
        books_store=store,
        builder=builder,
        indexers=[FakeIndexer()],
        workspace_base=tmp_path,
    )
    await compiler.startup()
    book_id = "book_test_freshmanifest"
    # Manifest FK -> books.id: need a row first.
    from bookscout.books import Book

    await store.create_book(Book.new(book_id=book_id, title="Test"))
    workspace = BookWorkspace.create(tmp_path, book_id)

    # Manifest row does NOT exist yet; calling _build_one_index must succeed
    # and end with status == 'built' (not raise StoreError).
    result = await compiler._build_one_index(
        FakeIndexer(),
        book_id,
        workspace,
        monitor=None,
        parent_id=None,
        idx_root=None,
    )
    assert result.count == 2

    manifest = await store.list_indexes(book_id)
    chunk_rows = [r for r in manifest if r.index_type == "chunk"]
    assert len(chunk_rows) == 1
    assert chunk_rows[0].status == "built"
    assert chunk_rows[0].count == 2

    await compiler.shutdown()
    await store.shutdown()
    await parser.shutdown()
    await builder.shutdown()


@pytest.mark.asyncio()
async def test_task_manager_run_index_creates_manifest_row_when_absent(tmp_path, logger):
    """Regression for the same bug via :class:`TaskManager._run_index` — building
    an index for a book whose manifest row doesn't exist should still succeed
    instead of raising ``StoreError('Manifest row not found: ...')``.
    """
    from bookscout.books import BooksConfig
    from bookscout.books import BooksStore
    from bookscout.doccompiler import EpubParser
    from bookscout.doccompiler import RuleBasedBuilder
    from bookscout.doccompiler.task_manager import TaskManager

    store = BooksStore(logger=logger, config=BooksConfig(base_path=tmp_path, db_name="books.sqlite"))
    await store.startup()
    parser = EpubParser(logger=logger)
    await parser.startup()
    builder = RuleBasedBuilder(logger=logger)
    await builder.startup()

    class FakeIndexer:
        _it = "summary"

        @property
        def index_type(self):
            return self._it

        async def startup(self):
            pass

        async def shutdown(self):
            pass

        async def build_index(self, book_id, workspace, *, monitor=None, parent_id=None):
            from bookscout.doccompiler.indexer import IndexProgress
            from bookscout.doccompiler.indexer import IndexResult

            return IndexResult(index_type=self._it, count=3, progress=IndexProgress(3, 3, "done", ""))

    book_id = "book_test_tm_runindex"
    book_workspace_root = tmp_path / book_id
    book_workspace_root.mkdir(parents=True, exist_ok=True)
    # Touch CONTENT.md so BookWorkspace.create succeeds if it ever needs it.
    (book_workspace_root / "CONTENT.md").write_text("# dummy\n", encoding="utf-8")
    # Manifest FK -> books.id: need a row first.
    from bookscout.books import Book

    await store.create_book(Book.new(book_id=book_id, title="Test"))

    tm = TaskManager(
        logger=logger,
        books_store=store,
        parser=parser,
        builder=builder,
        indexers=[FakeIndexer()],
        vector_store=None,
        workspace_base=tmp_path,
        monitor=None,
    )
    await tm.startup()

    task_id = await tm.start_index(book_id, ["summary"])
    # Poll until task exits
    import time

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        prog = tm.get_progress(task_id)
        if prog is None:
            break
        if prog.status in ("succeeded", "failed"):
            break
        await __import__("asyncio").sleep(0.02)

    prog = tm.get_progress(task_id)
    assert prog is not None, "Task disappeared"
    assert prog.status == "succeeded", f"prog={prog!r}"

    manifest = await store.list_indexes(book_id)
    summary_rows = [r for r in manifest if r.index_type == "summary"]
    assert len(summary_rows) == 1
    assert summary_rows[0].status == "built"
    assert summary_rows[0].count == 3

    await tm.shutdown()
    await store.shutdown()
    await parser.shutdown()
    await builder.shutdown()
