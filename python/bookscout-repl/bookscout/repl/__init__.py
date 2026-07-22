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
"""``bookscout.repl`` package 鈥?interactive agent runtime with streaming transport.

Public API:
    * :class:`BookScoutConfig` 鈥?configuration model (YAML + env + CLI).
    * :class:`ReplContext` 鈥?shared runtime resources (BooksStore, LLM,
      embedding, vector store, TaskManager, per-book ReadingMode cache).
    * :class:`Transport` 鈥?abstract transport for REPL communication.
    * :class:`StdioTransport` 鈥?stdio-based transport with length-prefixed JSON.
    * :class:`ReplServer` 鈥?the stdio REPL server (transport over ReplContext).
    * :class:`BookScoutTui` 鈥?the in-process Textual TUI.

CLI entry::

    bookscout-repl tui  --config config.yaml --set chatmodel.model=deepseek-v4-pro
    bookscout-repl serve --config config.yaml --daemon
"""

from __future__ import annotations

from .config import BookScoutConfig
from .context import ReplContext
from .transport import StdioTransport
from .transport import Transport
from .transport import WebSocketTransport

__all__ = [
    "BookScoutConfig",
    "ReplContext",
    "StdioTransport",
    "Transport",
    "WebSocketTransport",
]
