"""BookScout developer toolkit — top-level dispatcher.

Usage::

    bookscout-dev [OPTIONS] <COMMAND>
    bs [OPTIONS] <COMMAND>
"""

from __future__ import annotations

import typer

from scripts.bump import bump
from scripts.docs.__main__ import docs_app
from scripts.package.__main__ import package_app

app = typer.Typer(
    name="bookscout-dev",
    help="Developer toolkit for the BookScout monorepo",
    no_args_is_help=True,
)

app.add_typer(package_app, name="package")
app.add_typer(docs_app, name="docs")
app.command(name="bump")(bump)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
