"""SQLModel table for SummaryStore."""

from __future__ import annotations

from sqlmodel import Field
from sqlmodel import SQLModel


class SummaryModel(SQLModel, table=True):
    """Summary record associated with a BookNode."""

    __tablename__ = "summaries"

    id: int | None = Field(default=None, primary_key=True)
    book_id: str = Field(index=True, nullable=False)
    node_id: str = Field(index=True, nullable=False)
    node_title: str = Field(default="", nullable=False)
    level: int = Field(default=0, nullable=False)
    summary_text: str = Field(default="", nullable=False)
