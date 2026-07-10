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
"""``docs`` subcommand 鈥?documentation generation, building, and serving.

Exposes ``docs_app`` to be registered on the parent Typer app.
"""

from __future__ import annotations

import typer

from scripts.docs.build import build
from scripts.docs.gen import gen
from scripts.docs.serve import serve

docs_app = typer.Typer(name="docs", help="Documentation generation, building, and serving")
docs_app.command(name="gen")(gen)
docs_app.command(name="g", hidden=True)(gen)
docs_app.command(name="build")(build)
docs_app.command(name="b", hidden=True)(build)
docs_app.command(name="serve")(serve)
docs_app.command(name="s", hidden=True)(serve)

if __name__ == "__main__":
    docs_app()
