"""Book workspace — manages the on-disk directory structure for one book.

Each book gets a self-contained directory tree (spec §2.6)::

    {base_path}/{book_id}/
      source/           ← original source file copy
      CONTENT.md        ← unified normalised content
      parser/           ← parser mapping databases + artifacts
        mineru/         ← MinerU raw/merged results (PDF only)
          raw/
          merged/
      books.sqlite      ← ontology SQLite (managed by BooksStore)
      indexes/          ← derived layer indexes
        summary.sqlite
        chunks.sqlite
        graph.sqlite
        lancedb/
      reports/

The workspace uses **real filesystem paths** (not content-addressed blobs) so
that artifacts are directly inspectable. FileStore integration is available
for source-file storage; the workspace itself keeps visible files.
"""

from __future__ import annotations

import dataclasses
import pathlib


@dataclasses.dataclass(frozen=True, slots=True)
class BookWorkspace:
    """Filesystem layout for a single book's compilation artifacts.

    Attributes:
        root: Root directory ``{base_path}/{book_id}``.
        source_dir: Directory for the original source file copy.
        content_path: Path to ``CONTENT.md``.
        parser_dir: Directory for parser mapping DBs and artifacts.
        reports_dir: Directory for compilation reports.
    """

    root: pathlib.Path
    source_dir: pathlib.Path
    content_path: pathlib.Path
    parser_dir: pathlib.Path
    reports_dir: pathlib.Path

    @classmethod
    def create(cls, base_path: pathlib.Path | str, book_id: str) -> BookWorkspace:
        """Construct a workspace and create all directories on disk.

        Args:
            base_path: Base directory containing per-book subdirectories.
            book_id: The book id (used as the subdirectory name).

        Returns:
            A :class:`BookWorkspace` with all directories created.
        """
        root = pathlib.Path(base_path).resolve() / book_id
        ws = cls(
            root=root,
            source_dir=root / "source",
            content_path=root / "CONTENT.md",
            parser_dir=root / "parser",
            reports_dir=root / "reports",
        )
        ws._mkdirs()
        return ws

    def _mkdirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.parser_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    @property
    def indexes_dir(self) -> pathlib.Path:
        """Directory for derived layer index databases (spec §2.6)."""
        d = self.root / "indexes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def lancedb_dir(self) -> pathlib.Path:
        """Directory for LanceDB vector store data."""
        d = self.indexes_dir / "lancedb"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def mineru_raw_dir(self) -> pathlib.Path:
        """Directory for raw MinerU API response artifacts (per batch)."""
        d = self.parser_dir / "mineru" / "raw"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def mineru_merged_dir(self) -> pathlib.Path:
        """Directory for merged MinerU results across batches."""
        d = self.parser_dir / "mineru" / "merged"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def source_file_path(self, filename: str) -> pathlib.Path:
        """Return the path for a source file copy inside ``source/``.

        Args:
            filename: The filename to use (e.g. ``"original.epub"``).

        Returns:
            Full path under ``source/``.
        """
        return self.source_dir / filename

    def mapping_db_path(self, source_type: str) -> pathlib.Path:
        """Return the mapping SQLite path for a given source type.

        Args:
            source_type: ``"pdf"`` or ``"epub"``.

        Returns:
            Path like ``parser/pdf.sqlite`` or ``parser/epub.sqlite``.
        """
        return self.parser_dir / f"{source_type}.sqlite"

    def index_db_path(self, name: str) -> pathlib.Path:
        """Return the index SQLite path for a derived layer index.

        Args:
            name: Index name (e.g. ``"summary"``, ``"chunks"``, ``"graph"``).

        Returns:
            Path like ``indexes/summary.sqlite``.
        """
        return self.indexes_dir / f"{name}.sqlite"
