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
"""PDF parser abstract base (spec 搂5.5).

``PdfParser`` adds PDF-specific concerns on top of :class:`DocParser`.
The current concrete implementation is
:class:`~bookscout.doccompiler.parser.pdf.mineruapi.MineruPdfParser`.
"""

from __future__ import annotations

import abc
import pathlib

from ...types import ParserResult
from ...workspace import BookWorkspace
from .. import DocParser


class PdfParser(DocParser, abc.ABC):
    """Abstract base for PDF parsers."""

    @abc.abstractmethod
    async def parse(
        self,
        source_path: pathlib.Path,
        book_id: str,
        workspace: BookWorkspace,
    ) -> ParserResult:
        """Parse a PDF into CONTENT.md + PDF mapping SQLite.

        Args:
            source_path: Path to the ``.pdf`` file.
            book_id: The book id.
            workspace: The book workspace for output artifacts.

        Returns:
            A :class:`ParserResult`.
        """
