"""``docs`` subcommand — documentation generation, building, and serving.

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
