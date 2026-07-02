"""Typer CLI for bookscout-doccompiler (spec §13, §16.7).

Provides ``bookscout-doccompiler compile <source_path>`` command.
CLI dependencies (typer, rich) are in the ``cli`` extra::

    pip install bookscout-doccompiler[cli]
"""

from __future__ import annotations

import asyncio
import pathlib
import typing as t

if t.TYPE_CHECKING:
    import typer
else:
    try:
        import typer
    except ImportError:
        typer = None  # type: ignore[assignment]


def _create_app() -> t.Any:
    """Create the Typer app. Called lazily to avoid import errors."""
    if typer is None:
        raise ImportError(
            "CLI dependencies not installed. Install with: pip install bookscout-doccompiler[cli]"
        ) from None

    app = typer.Typer(  # pylint: disable=redefined-outer-name
        name="bookscout-doccompiler",
        help="BookScout document compiler — parse, build ontology, persist.",
        no_args_is_help=True,
    )

    @app.command()
    def compile(  # pylint: disable=redefined-builtin
        source_path: pathlib.Path = typer.Argument(help="Path to the source file (EPUB, PDF)."),
        build_mode: str = typer.Option(
            "rule",
            "--mode",
            "-m",
            help="Build mode: 'rule' (fast, heading-based) or 'llm_tool' (LLM tool-driven).",
        ),
        output_dir: pathlib.Path = typer.Option(
            pathlib.Path("output"),
            "--output",
            "-o",
            help="Output directory for compiled artifacts.",
        ),
        use_llm_metadata: bool = typer.Option(
            False,
            "--llm-metadata",
            help="Use LLM for metadata extraction (requires DEEPSEEK_API_KEY).",
        ),
        build_indexes: bool = typer.Option(
            False,
            "--indexes",
            help="Build derived indexes (summary, chunk, graph) after compilation. Requires LLM + embedding.",
        ),
        parser_type: str = typer.Option(
            "auto",
            "--parser",
            "-p",
            help="Parser type: 'auto', 'epub', 'pdf'. Auto-detects from file extension.",
        ),
    ) -> None:
        """Compile a source document into a BookScout ontology."""
        import os

        from dotenv import load_dotenv

        from bookscout.books import BooksConfig
        from bookscout.books import BooksStore
        from bookscout.doccompiler import EpubParser
        from bookscout.doccompiler import LlmToolBuilder
        from bookscout.doccompiler import MineruPdfParser
        from bookscout.doccompiler import RuleBasedBuilder
        from bookscout.doccompiler.compiler import Compiler
        from bookscout.logging import LoggingConfig
        from bookscout.logging import build_logger

        load_dotenv()

        if not source_path.exists():
            typer.echo(f"ERROR: source file not found: {source_path}", err=True)
            raise typer.Exit(1) from None

        # Auto-detect parser type.
        ext = source_path.suffix.lower()
        if parser_type == "auto":
            if ext == ".epub":
                parser_type = "epub"
            elif ext == ".pdf":
                parser_type = "pdf"
            else:
                typer.echo(f"ERROR: cannot auto-detect parser for extension '{ext}'", err=True)
                raise typer.Exit(1) from None

        # Build logger.
        logger = build_logger(
            LoggingConfig(
                name="doccompiler-cli",
                level="INFO",
                targets=[{"dest": "stdout", "level": "INFO", "pretty": True}],
            )
        )

        # Build parser.
        if parser_type == "epub":
            parser = EpubParser(logger=logger)
        elif parser_type == "pdf":
            parser = MineruPdfParser(logger=logger)
        else:
            typer.echo(f"ERROR: unknown parser type: {parser_type}", err=True)
            raise typer.Exit(1) from None

        # Build LLM model if requested (stateless mode).
        llm_model = None
        if use_llm_metadata or build_mode == "llm_tool" or build_indexes:
            api_key = os.environ.get("DEEPSEEK_API_KEY", "")
            if not api_key:
                typer.echo(
                    "WARNING: LLM features requested but DEEPSEEK_API_KEY not found. "
                    "LLM metadata, llm_tool builder, and indexes will be skipped.",
                    err=True,
                )
            else:
                from bookscout.llm.config import LLMConfig
                from bookscout.llm.config import OpenAIConfig
                from bookscout.llm.openai import OpenAIChatModel

                llm_model = OpenAIChatModel(
                    logger=logger,
                    config=LLMConfig(
                        backend=OpenAIConfig(
                            api_key=api_key,
                            base_url=os.environ.get("DEEPSEEK_OPENAI_BASE_URL", "https://api.deepseek.com"),
                            model="deepseek-chat",
                        ),
                        stateless=True,
                    ),
                )

        # Build the appropriate builder.
        if build_mode == "llm_tool" and llm_model is not None:
            builder = LlmToolBuilder(logger=logger, model=llm_model)
        else:
            builder = RuleBasedBuilder(logger=logger)

        # Build books store.
        books_store = BooksStore(
            logger=logger,
            config=BooksConfig(base_path=output_dir, db_name="books.sqlite"),
        )

        # Build indexers if requested.
        indexers: list[t.Any] = []
        if build_indexes and llm_model is not None:
            dashscope_key = os.environ.get("DASHSCOPE_EMBEDDING_API_KEY", "")
            if not dashscope_key:
                typer.echo(
                    "WARNING: --indexes set but DASHSCOPE_EMBEDDING_API_KEY not found. Skipping indexes.", err=True
                )
            else:
                from bookscout.embedding.openai import OpenAIEmbedding
                from bookscout.embedding.openai import OpenAIEmbeddingConfig
                from bookscout.index.chunk import ChunkIndexer
                from bookscout.index.graph import GraphIndexer
                from bookscout.index.summary import SummaryIndexer
                from bookscout.llm import ChatModel
                from bookscout.vectorstore.lancedb import LanceDBConfig
                from bookscout.vectorstore.lancedb import LanceDBStore

                embedding = OpenAIEmbedding(
                    OpenAIEmbeddingConfig(
                        api_key=dashscope_key,
                        base_url=os.environ.get(
                            "DASHSCOPE_EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
                        ),
                        model=os.environ.get("DASHSCOPE_EMBEDDING_MODEL", "text-embedding-v4"),
                        batch_size=10,
                    )
                )
                vector_store = LanceDBStore(
                    LanceDBConfig(
                        uri=str(output_dir / "lancedb"),
                        table_name="bookscout_vectors",
                    )
                )

                estimate_fn = ChatModel.estimate_token
                indexers = [
                    SummaryIndexer(logger=logger, books_store=books_store, model=llm_model),
                    ChunkIndexer(
                        logger=logger,
                        books_store=books_store,
                        embedding=embedding,
                        vector_store=vector_store,
                        estimate_token_fn=estimate_fn,
                    ),
                    GraphIndexer(
                        logger=logger,
                        books_store=books_store,
                        model=llm_model,
                        embedding=embedding,
                        vector_store=vector_store,
                        estimate_token_fn=estimate_fn,
                    ),
                ]

        # Build compiler.
        compiler = Compiler(
            logger=logger,
            parser=parser,
            books_store=books_store,
            builder=builder,
            llm_model=llm_model,
            indexers=indexers if indexers else None,
            workspace_base=output_dir,
        )

        # Run compilation.
        try:
            asyncio.run(_run_compile(compiler, source_path))
        except Exception as e:
            typer.echo(f"ERROR: compilation failed: {e}", err=True)
            raise typer.Exit(1) from e
        finally:
            logger.flush()
            logger.close()

    return app


async def _run_compile(compiler: t.Any, source_path: pathlib.Path) -> None:
    """Run the compilation coroutine and print results."""
    import typer

    await compiler.startup()
    try:
        result = await compiler.compile(source_path)
        m = result.metrics
        typer.echo("")
        typer.echo("=" * 60)
        typer.echo("Compilation succeeded!")
        typer.echo("=" * 60)
        typer.echo(f"  Book ID:       {m.book_id}")
        typer.echo(f"  Title:         {result.book.title}")
        typer.echo(f"  Author:        {result.book.author}")
        typer.echo(f"  Content path:  {m.content_path}")
        typer.echo(f"  Mapping DB:    {m.mapping_db_path}")
        typer.echo(f"  Nodes:         {m.node_count}")
        typer.echo(f"  Content chars: {m.total_chars}")
        typer.echo(f"  Workspace:     {result.workspace.root}")
        typer.echo(f"  Status:        {m.status}")
        typer.echo(f"  Stage:         {m.stage}")
        typer.echo("")
        typer.echo("Metrics:")
        typer.echo(f"  warnings:      {m.warning_count}")
        typer.echo(f"  errors:        {m.error_count}")
        typer.echo(f"  rollbacks:     {m.rollback_count}")
        typer.echo(f"  started_at:    {m.started_at}")
        typer.echo(f"  finished_at:   {m.finished_at}")
    finally:
        await compiler.shutdown()


# Create the app lazily — only when imported with typer available.
app = _create_app() if typer is not None else None


def main() -> None:
    """CLI entry point."""
    if app is None:
        print("CLI dependencies not installed. Install with: pip install bookscout-doccompiler[cli]")
        raise SystemExit(1)
    app()
