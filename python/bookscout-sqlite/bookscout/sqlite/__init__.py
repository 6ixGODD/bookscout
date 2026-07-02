from __future__ import annotations

import typing as t

from pydantic import BaseModel
from pydantic.fields import Field
from sqlalchemy import pool
from sqlalchemy import text
from sqlalchemy.event import listens_for
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

from .exceptions import SQLiteConnectionError

if t.TYPE_CHECKING:
    from sqlalchemy import Connection
    from sqlalchemy import Result
    from sqlalchemy.ext.asyncio import AsyncEngine

    from bookscout.logging import Logger

    class SQLitePoolConfig(t.TypedDict):
        echo: bool
        """Whether to log all SQL statements."""

        pool_size: int
        """Number of connections to maintain in the pool."""

        max_overflow: int
        """Max number of connections beyond pool_size."""

        pool_timeout: float
        """Seconds to wait before timing out on connection."""

        pool_recycle: int
        """Seconds after which to recycle connections."""

        pool_pre_ping: bool
        """Test connections before using them."""


class SQLiteConfig(BaseModel):
    uri: str = Field(
        default="sqlite+aiosqlite:///./database.db",
        description=(
            "SQLite connection URI. Must use aiosqlite driver for async support. "
            "Examples: "
            "- 'sqlite+aiosqlite:///./database.db' (relative path) "
            "- 'sqlite+aiosqlite:////absolute/path/database.db' (absolute path) "
            "- 'sqlite+aiosqlite:///:memory:' (in-memory database)"
        ),
        pattern=r"^sqlite\+aiosqlite:///(?:[^/]+/)*[^/]+\.(db|sqlite|sqlite3)$|^sqlite\+aiosqlite:////(?:[^/]+/)*[^/]+\.(db|sqlite|sqlite3)$|^sqlite\+aiosqlite:///:memory:$",
    )

    echo: bool = Field(
        default=False,
        description="Whether to log all SQL statements (useful for debugging).",
    )

    pool_size: int = Field(
        default=20,
        description=(
            "Number of connections to maintain in the pool. Note: SQLite with "
            "aiosqlite uses NullPool by default in async mode."
        ),
        ge=1,
        le=100,
    )

    max_overflow: int = Field(
        default=10,
        description="Max number of connections beyond pool_size.",
        ge=0,
        le=100,
    )

    pool_timeout: float = Field(
        default=30.0,
        description="Seconds to wait before timing out on connection.",
        ge=1.0,
        le=120.0,
    )

    pool_recycle: int = Field(
        default=3600,
        description="Seconds after which to recycle connections. Set to -1 to disable.",
        ge=-1,
        le=86400,
    )

    pool_pre_ping: bool = Field(
        default=True,
        description="Test connections before using them. Recommended for production to handle stale connections.",
    )


class SQLite(LoggingMixin, AsyncResourceMixin):
    """SQLite database container with async SQLModel/SQLAlchemy support.

    This class provides a high-level interface for SQLite database
    operations with the following features:

        1. Async engine and session management for repository pattern
        2. Connection pooling with configurable parameters
        3. Raw SQL execution with transaction control
        4. Schema management utilities (``create_all``/``drop_all``)
        5. Unified lifecycle management through :class:`AsyncResourceMixin`

    Attributes:
        uri: SQLite connection URI.
        engine: SQLAlchemy async engine (initialized after :meth:`init`).
        sessionmaker: Async session factory (initialized after :meth:`init`).
        pool_config: Connection pool configuration.

    Example::

        # Setup with file-based database
        sqlite = SQLite(
            uri="sqlite+aiosqlite:///./app.db",
            tables=[User, Post],
            echo=True,
        )

        # Setup with in-memory database (useful for testing)
        sqlite = SQLite(
            uri="sqlite+aiosqlite:///:memory:",
            tables=[User, Post],
        )

        # Initialize
        await sqlite.init()

        # Create tables
        await sqlite.create_all()

        # Use session for ORM operations
        async with sqlite.session() as session:
            user = await session.get(User, user_id)
            user.username = "new_name"
            await session.commit()

        # Execute raw SQL
        result = await sqlite.exec(
            "SELECT * FROM users WHERE age > :age",
            readonly=True,
            age=21,
        )

        # Cleanup
        await sqlite.close()

    Note:
        - The URI must use the aiosqlite driver for async support.
        - SQLite doesn't support some PostgreSQL features (e.g., JSONB operators).
        - Use JSON1 extension for JSON operations (enabled by default).
        - File path format: Use three slashes for relative paths, four for absolute.
    """

    __logtag__ = "infra.SQLITE"

    def __init__(self, config: SQLiteConfig, logger: Logger):
        super().__init__(logger=logger)
        self.config = config
        self.uri = config.uri
        self.engine: AsyncEngine | None = None
        self.sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self.pool_config: SQLitePoolConfig = {
            "echo": config.echo,
            "pool_size": config.pool_size,
            "max_overflow": config.max_overflow,
            "pool_timeout": config.pool_timeout,
            "pool_recycle": config.pool_recycle,
            "pool_pre_ping": config.pool_pre_ping,
        }

    async def startup(self) -> None:
        """Initialize the database engine and session factory.

        This method creates the async engine with connection pooling and
        sets up the session factory. It should be called during application
        startup, typically in a lifespan context manager.

        For SQLite, this also enables foreign key constraints and loads
        the JSON1 extension if available.

        Raises:
            Exception: If engine creation fails (e.g., invalid URI).
        """
        # Create engine with SQLite-specific configuration
        try:
            self.engine = create_async_engine(
                self.uri,
                echo=self.pool_config["echo"],
                # SQLite-specific: Use NullPool for better async compatibility
                # or StaticPool for in-memory databases
                poolclass=pool.NullPool if ":memory:" not in self.uri else pool.StaticPool,
                connect_args={
                    "check_same_thread": False,  # Required for async SQLite
                },
            )

            # Configure SQLite settings
            @listens_for(self.engine.sync_engine, "connect")
            def set_sqlite_pragma(dbapi_conn: t.Any, _connection_record: t.Any) -> None:
                """Set SQLite-specific pragmas on connection."""
                cursor = dbapi_conn.cursor()
                # Enable foreign key constraints
                cursor.execute("PRAGMA foreign_keys=ON")
                # Enable WAL mode for better concurrency
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()

            self.sessionmaker = async_sessionmaker(
                self.engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        except Exception as e:
            raise SQLiteConnectionError(
                error_message="Failed to initialize SQLite engine",
                uri=self.uri,
            ) from e

        await super().startup()

    async def shutdown(self) -> None:
        """Close the database engine and clean up resources.

        This method disposes of the connection pool and resets the engine
        and session factory. It should be called during application shutdown.

        Note:
            This method is idempotent and safe to call multiple times.
        """
        if self.engine:
            await self.engine.dispose()
            self.engine = None
            self.sessionmaker = None

    def session(self) -> AsyncSession:
        """Create a new async database session.

        Returns:
            An async session context manager.

        Raises:
            RuntimeError: If sessionmaker is not initialized (call init() first).

        Example:
            ```python
            async with sqlite.session() as session:
                # Start a transaction
                user = await session.get(User, user_id)
                user.username = "new_name"
                await session.commit()
            ```

        Note:
            The session is automatically committed on successful exit and
            rolled back on exception. You can also manually commit/rollback
            within the context.
        """
        if not self.sessionmaker:
            raise RuntimeError("Sessionmaker not initialized. Call init() first.")

        return self.sessionmaker()

    async def exec(self, sql: str, /, readonly: bool = False, **params: t.Any) -> Result[t.Any]:
        """Execute a raw SQL statement.

        This method provides direct SQL execution for cases where ORM
        abstractions are insufficient or when specific optimizations
        are needed.

        Args:
            sql: Raw SQL string to execute. Use named parameters with
                colon prefix.
            readonly: If True, does not commit the transaction. Use this
                for SELECT queries to avoid unnecessary commits.
            **params: Named parameters for the SQL statement.

        Returns:
            SQLAlchemy Result object containing query results.

        Raises:
            RuntimeError: If execution fails, with the original exception
                as the cause.

        Example:
            ```python
            # Read-only query
            result = await sqlite.exec(
                "SELECT * FROM users WHERE age > :age",
                readonly=True,
                age=21,
            )
            users = result.fetchall()

            # Write query
            await sqlite.exec(
                "UPDATE users SET status = :status WHERE id = :id",
                readonly=False,
                status="active",
                id=123,
            )

            # Using JSON1 extension
            result = await sqlite.exec(
                "SELECT * FROM users WHERE json_extract(tags, '$.premium') = 1",
                readonly=True,
            )
            ```

        Warning:
            Be careful with SQL injection. Always use parameterized queries
            with named parameters instead of string formatting.
        """
        async with self.session() as session, session.begin():
            result = await session.execute(text(sql), params=params or None)
            if not readonly:
                await session.commit()
            return result

    async def ping(self) -> bool:
        """Check database connectivity.

        This method attempts to execute a simple query to verify that
        the database is reachable and responsive.

        Returns:
            True if database is reachable, False otherwise.

        Note:
            This method does not raise exceptions. It catches all errors
            and returns False instead.
        """
        if not self.engine:
            return False

        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        # pylint: disable-next=broad-exception-caught
        except Exception:
            return False

    async def create_all(self, tables: list[type[SQLModel]]) -> None:
        """Create all database tables.

        This method creates tables for the specified SQLModel classes, or
        all tables in the SQLModel metadata if no models are specified.

        Raises:
            RuntimeError: If engine is not initialized.

        Example:
            ```python
            # Create specific tables
            sqlite = SQLite(
                uri="sqlite+aiosqlite:///./app.db",
                tables=[User, Post, Comment],
            )
            await sqlite.init()
            await sqlite.create_all()

            # Create all tables
            sqlite = SQLite(uri="sqlite+aiosqlite:///./app.db")
            await sqlite.init()
            await sqlite.create_all()
            ```

        Warning:
            This is typically used for development/testing. In production,
            use proper migration tools like Alembic to manage schema changes.
        """
        if not self.engine:
            raise RuntimeError("Engine not initialized. Call init() first.")

        async with self.engine.begin() as conn:  # type: ignore

            def _create_tables(sync_conn: Connection) -> None:
                from sqlalchemy import Table

                # Only create the specific tables passed in, not the entire
                # global SQLModel metadata (which would leak all registered
                # table=True models into every database).
                for model in tables:
                    table: Table = model.__table__  # type: ignore[attr-defined]
                    table.create(bind=sync_conn, checkfirst=True)

            await conn.run_sync(_create_tables)

    async def drop_all(self) -> None:
        """Drop all database tables.

        This method drops all tables defined in the SQLModel metadata.

        Raises:
            RuntimeError: If engine is not initialized.

        Example:
            ```python
            await sqlite.drop_all()  # Be careful!
            ```

        Warning:
            This is destructive and should only be used in development/testing.
            All data will be lost. There is no confirmation prompt.
        """
        if not self.engine:
            raise RuntimeError("Engine not initialized. Call init() first.")

        async with self.engine.begin() as conn:  # type: ignore
            await conn.run_sync(SQLModel.metadata.drop_all)

    async def vacuum(self) -> None:
        """Run VACUUM command to optimize the database file.

        This command rebuilds the database file, repacking it into a minimal
        amount of disk space. It's useful after deleting large amounts of data.

        Raises:
            RuntimeError: If engine is not initialized.

        Example:
            ```python
            # After bulk deletions
            await sqlite.exec(
                "DELETE FROM old_logs WHERE created_at < :date",
                date=cutoff_date,
            )
            await sqlite.vacuum()  # Reclaim disk space
            ```

        Note:
            VACUUM requires exclusive access to the database and may take
            significant time on large databases.
        """
        if not self.engine:
            raise RuntimeError("Engine not initialized. Call init() first.")

        # VACUUM must be run outside a transaction
        async with self.engine.connect() as conn:
            await conn.execute(text("VACUUM"))
            await conn.commit()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(config={self.config!r})"
