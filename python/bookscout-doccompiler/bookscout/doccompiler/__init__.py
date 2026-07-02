"""``bookscout.doccompiler`` — document compiler.

Public API:
    * :class:`DocParser` — abstract parser base.
    * :class:`EpubParser`, :class:`MineruPdfParser` — concrete parsers.
    * :class:`Builder`, :class:`BuildResult` — builder abstraction.
    * :class:`RuleBasedBuilder`, :class:`LlmToolBuilder` — concrete builders.
    * :class:`Indexer`, :class:`IndexResult`, :class:`IndexProgress` — indexer abstraction.
    * :class:`Compiler`, :class:`CompileMetrics` — compiler.
    * :class:`BookWorkspace` — per-book workspace.
    * :class:`SourceInfo`, :class:`ParserResult` — parser data types.
"""

from __future__ import annotations

from .builder import BuildResult
from .builder import Builder
from .builder.llm_tool import LlmToolBuilder
from .builder.rule import RuleBasedBuilder
from .compiler import CompileMetrics
from .compiler import CompileResult
from .compiler import CompileStage
from .compiler import CompileStatus
from .compiler import Compiler
from .indexer import IndexProgress
from .indexer import IndexResult
from .indexer import Indexer
from .parser import DocParser
from .parser.epub import EpubParser
from .parser.pdf import PdfParser
from .parser.pdf.mineruapi import MineruPdfParser
from .types import EpubSourceMapping
from .types import ParserResult
from .types import PdfSourceMapping
from .types import SourceInfo
from .workspace import BookWorkspace

__all__ = [
    "BookWorkspace",
    "BuildResult",
    "Builder",
    "CompileMetrics",
    "CompileResult",
    "CompileStage",
    "CompileStatus",
    "Compiler",
    "DocParser",
    "EpubParser",
    "EpubSourceMapping",
    "IndexProgress",
    "IndexResult",
    "Indexer",
    "LlmToolBuilder",
    "MineruPdfParser",
    "ParserResult",
    "PdfParser",
    "PdfSourceMapping",
    "RuleBasedBuilder",
    "SourceInfo",
]
