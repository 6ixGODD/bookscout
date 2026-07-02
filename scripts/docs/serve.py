"""``docs serve`` — Preview the documentation site with MkDocs."""

from __future__ import annotations

import socket
import subprocess
import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.workspace import ROOT


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def serve(
    host: Annotated[
        str,
        typer.Option("--host", help="Bind host for the dev server"),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("-p", "--port", help="Port for the dev server (0 = find a free port)"),
    ] = 0,
    open_browser: Annotated[
        bool,
        typer.Option("--open", help="Open the browser automatically"),
    ] = False,
) -> None:
    """Start the MkDocs development server to preview documentation."""
    actual_port = port if port > 0 else _find_free_port()
    console.info(f"Starting MkDocs dev server on http://{host}:{actual_port}")
    cmd = [
        sys.executable,
        "-m",
        "mkdocs",
        "serve",
        "--dev-addr",
        f"{host}:{actual_port}",
    ]
    if open_browser:
        cmd.append("--open")
    try:
        subprocess.run(cmd, cwd=str(ROOT), check=True)
    except KeyboardInterrupt:
        console.info("Dev server stopped.")
    except subprocess.CalledProcessError as exc:
        console.error(f"mkdocs serve failed with exit code {exc.returncode}")
        sys.exit(exc.returncode)
