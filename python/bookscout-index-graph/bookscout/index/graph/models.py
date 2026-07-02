"""SQLModel tables for GraphStore."""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy import Column
from sqlmodel import Field
from sqlmodel import SQLModel


class EntityModel(SQLModel, table=True):
    """Entity record in the knowledge graph."""

    __tablename__ = "entities"

    id: str = Field(primary_key=True)
    book_id: str = Field(index=True, nullable=False)
    name: str = Field(nullable=False, index=True)
    entity_type: str = Field(default="Unknown", nullable=False)
    tags_json: list[str] | None = Field(default=None, sa_column=Column("tags_json", JSON))
    summary: str = Field(default="", nullable=False)
    source_chunk_id: str = Field(default="", nullable=False)
    source_node_id: str = Field(default="", nullable=False, index=True)


class RelationshipModel(SQLModel, table=True):
    """Relationship record in the knowledge graph.

    Uses entity_id (not entity_name) to reference source and target
    entities, so that同名不同 type 的 entities 不会被误连。
    """

    __tablename__ = "relationships"

    id: str = Field(primary_key=True)
    book_id: str = Field(index=True, nullable=False)
    source_entity_id: str = Field(nullable=False, index=True)
    target_entity_id: str = Field(nullable=False, index=True)
    relation_type: str = Field(default="", nullable=False)
    summary: str = Field(default="", nullable=False)
    claims_json: list[str] | None = Field(default=None, sa_column=Column("claims_json", JSON))
    source_chunk_id: str = Field(default="", nullable=False)
    source_node_id: str = Field(default="", nullable=False)
    content_offset: int = Field(default=0, nullable=False)
    content_length: int = Field(default=0, nullable=False)
