"""``docs build`` — Build the MkDocs static site."""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.workspace import ROOT


def build(
    strict: Annotated[
        bool,
        typer.Option(
            "--strict",
            help="Enable strict mode — warn on any broken link or reference",
        ),
    ] = False,
    output: Annotated[
        str,
        typer.Option(
            "-o",
            "--output",
            help="Output directory for the built site (default: site/)",
        ),
    ] = "site",
) -> None:
    """Build the MkDocs static documentation site.

    Builds the site into the output directory (default: ``site/``).
    Use ``--strict`` to fail on any warnings (broken links, missing refs).

    Typical workflow::

        bs docs gen        # generate API reference stubs
        bs docs build      # build the static site
    """
    out_dir = (ROOT / output).resolve()

    cmd = [
        sys.executable,
        "-m",
        "mkdocs",
        "build",
        "--site-dir",
        str(out_dir),
    ]
    if strict:
        cmd.append("--strict")

    console.header(f"Building MkDocs site  →  {out_dir}")
    print()

    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )

    if result.stdout:
        for line in result.stdout.strip().splitlines():
            print(f"  {console.format_dim(line)}")

    if result.returncode == 0:
        print()
        console.success(f"Documentation site built to {out_dir}")
    else:
        print()
        if result.stderr:
            for line in result.stderr.strip().splitlines():
                console.error(f"  {line}")
        console.error("mkdocs build failed.")
        sys.exit(result.returncode)
