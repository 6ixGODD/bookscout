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
"""`bookscout.books` package 鈥?the BookScout ontology layer.

Defines :class:`Book` and :class:`BookNode` (immutable domain models) plus
:class:`BooksStore`, the single entry point for persisting and querying the
ontology. SQLite is an implementation detail of ``BooksStore`` and is never
exposed to callers.

See ``experimental/req/data-layer.md`` 搂2.1 and 搂3 for the full specification.
"""

from __future__ import annotations

from .exceptions import BookExistsError
from .exceptions import BookNotFoundError
from .exceptions import BooksError
from .exceptions import ContentError
from .exceptions import NodeNotFoundError
from .exceptions import StoreError
from .exceptions import TreeValidationError
from .store import BooksConfig
from .store import BooksStore
from .types import Book
from .types import BookNode

__all__ = [
    "Book",
    "BookExistsError",
    "BookNode",
    "BookNotFoundError",
    "BooksConfig",
    "BooksError",
    "BooksStore",
    "ContentError",
    "NodeNotFoundError",
    "StoreError",
    "TreeValidationError",
]
