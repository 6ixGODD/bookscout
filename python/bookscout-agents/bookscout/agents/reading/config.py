"""Configuration for the reading agent and mode."""

from __future__ import annotations

import pathlib

import pydantic


class ReadingLLMProfiles(pydantic.BaseModel):
    """Model profiles used by :class:`ReadingAgent`."""

    cheap: str | None = None
    standard: str | None = None
    strong: str | None = None


class ReadingModeConfig(pydantic.BaseModel):
    """Configuration for a reading session over one indexed book."""

    books_base_path: pathlib.Path | str
    book_id: str
    db_uri: str
    conversation_id: str | None = None
    books_db_base_path: pathlib.Path | str | None = None
    summary_db_path: pathlib.Path | str | None = None
    chunk_db_path: pathlib.Path | str | None = None
    graph_db_path: pathlib.Path | str | None = None
    lancedb_uri: pathlib.Path | str | None = None
    llm_profiles: ReadingLLMProfiles = pydantic.Field(default_factory=ReadingLLMProfiles)
    lancedb_table_name: str = "bookscout_vectors"

    model_config = pydantic.ConfigDict(arbitrary_types_allowed=True)

    @property
    def workspace_root(self) -> pathlib.Path:
        return pathlib.Path(self.books_base_path).expanduser().resolve() / self.book_id

    @property
    def books_store_base_path(self) -> pathlib.Path:
        if self.books_db_base_path is not None:
            return pathlib.Path(self.books_db_base_path).expanduser().resolve()
        return self.workspace_root

    @property
    def resolved_summary_db_path(self) -> pathlib.Path:
        return self._resolve_index_path(self.summary_db_path, "summary.sqlite")

    @property
    def resolved_chunk_db_path(self) -> pathlib.Path:
        return self._resolve_index_path(self.chunk_db_path, "chunks.sqlite")

    @property
    def resolved_graph_db_path(self) -> pathlib.Path:
        return self._resolve_index_path(self.graph_db_path, "graph.sqlite")

    @property
    def resolved_lancedb_uri(self) -> str:
        if self.lancedb_uri is not None:
            return str(pathlib.Path(self.lancedb_uri).expanduser().resolve())
        return str(self.workspace_root / "indexes" / "lancedb")

    def _resolve_index_path(self, value: pathlib.Path | str | None, filename: str) -> pathlib.Path:
        if value is not None:
            return pathlib.Path(value).expanduser().resolve()
        return self.workspace_root / "indexes" / filename
