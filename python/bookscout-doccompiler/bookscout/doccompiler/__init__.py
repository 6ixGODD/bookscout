# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""``bookscout.doccompiler`` 鈥?document compiler.

Public API:
    * :class:`DocParser` 鈥?abstract parser base.
    * :class:`EpubParser`, :class:`MineruPdfParser` 鈥?concrete parsers.
    * :class:`Builder`, :class:`BuildResult` 鈥?builder abstraction.
    * :class:`RuleBasedBuilder`, :class:`LlmToolBuilder` 鈥?concrete builders.
    * :class:`Indexer`, :class:`IndexResult`, :class:`IndexProgress` 鈥?indexer abstraction.
    * :class:`Compiler`, :class:`CompileMetrics` 鈥?compiler.
    * :class:`BookWorkspace` 鈥?per-book workspace.
    * :class:`SourceInfo`, :class:`ParserResult` 鈥?parser data types.
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
from .index_provider import IndexContext
from .index_provider import IndexProvider
from .index_registry import IndexRegistry
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
    "IndexContext",
    "IndexProgress",
    "IndexProvider",
    "IndexRegistry",
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
