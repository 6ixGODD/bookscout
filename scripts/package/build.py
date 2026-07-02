"""``pkg build`` — build all (or selected) Python packages into wheels."""

from __future__ import annotations

import subprocess
import sys
from typing import Annotated

import typer

from scripts._helpers import console
from scripts._helpers.workspace import PYTHON_DIR
from scripts._helpers.workspace import ROOT
from scripts._helpers.workspace import get_existing_packages

_DEFAULT_OUT = "wheelhouse"


def build(
    out: Annotated[
        str,
        typer.Option("-o", "--out", help=f"Output directory for wheels (default: {_DEFAULT_OUT}/)"),
    ] = _DEFAULT_OUT,
    packages: Annotated[
        list[str],
        typer.Option("-p", "--package", help="Package name(s) to build (may be repeated); default: all"),
    ] = [],  # noqa: B006
    clean: Annotated[
        bool,
        typer.Option("-c", "--clean", help="Remove existing *.whl files in output dir before building"),
    ] = False,
) -> None:
    """Build Python packages into wheels.

    By default builds every package under python/.  Use --package to
    restrict to one or more specific packages.
    """
    out_dir = (ROOT / out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    all_packages = get_existing_packages()
    if not all_packages:
        console.error("No packages found in python/")
        sys.exit(1)

    # Resolve which packages to build
    if packages:
        unknown = [p for p in packages if p not in all_packages]
        if unknown:
            console.error(f"Unknown package(s): {', '.join(unknown)}")
            console.info(f"Available: {', '.join(all_packages)}")
            sys.exit(1)
        targets = [p for p in all_packages if p in packages]
    else:
        targets = all_packages

    if clean:
        removed = list(out_dir.glob("*.whl"))
        for whl in removed:
            whl.unlink()
        if removed:
            console.info(f"Removed {len(removed)} existing wheel(s) from {out_dir}")

    console.header(f"Building {len(targets)} package(s)  →  {out_dir}")
    print()

    ok: list[str] = []
    failed: list[str] = []

    for pkg_name in targets:
        pkg_dir = PYTHON_DIR / pkg_name
        console.step(f"Building {console.format_cyan(pkg_name)} …")
        result = subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
            cwd=pkg_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # Find the wheel(s) just produced
            wheels = sorted(out_dir.glob(f"{pkg_name.replace('-', '_')}*.whl"))
            whl_name = wheels[-1].name if wheels else "?"
            console.success(f"  {whl_name}")
            ok.append(pkg_name)
        else:
            console.error(f"  Failed to build {pkg_name}")
            if result.stderr:
                for line in result.stderr.strip().splitlines():
                    print(f"    {console.format_dim(line)}")
            failed.append(pkg_name)

    print()
    console.info(
        f"{console.format_success(str(len(ok)))} built, "
        f"{console.format_error(str(len(failed))) if failed else '0'} failed"
        f"  →  {out_dir}"
    )

    if failed:
        sys.exit(1)
