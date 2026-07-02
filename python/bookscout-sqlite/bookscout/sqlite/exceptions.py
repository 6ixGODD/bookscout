from __future__ import annotations


class SQLiteError(Exception):
    """Base exception for all SQLite-related errors."""


class ExecFailedError(SQLiteError):
    """Raised when a SQLite command execution fails.

    Attributes:
        command: The SQL command that was attempted.
        error_message: The error message describing the failure.
    """

    def __init__(self, command: str, error_message: str) -> None:
        self.command = command
        self.error_message = error_message
        super().__init__(f"Failed to execute SQLite command '{command}': {error_message}")


class SQLiteConnectionError(SQLiteError):
    """Raised when a connection to SQLite cannot be established.

    Attributes:
        uri: The URI of the SQLite database that was attempted to connect to.
        error_message: The error message describing the connection failure.
    """

    def __init__(self, uri: str, error_message: str) -> None:
        self.uri = uri
        self.error_message = error_message
        super().__init__(f"Failed to connect to SQLite at {uri}: {error_message}")
