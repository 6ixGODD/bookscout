"""BookScout REPL CLI entry point — Typer-based launcher.

Subcommands::

    bookscout-repl tui   --config config.yaml --set chatmodel.model=...
    bookscout-repl serve --config config.yaml [--daemon]

Both subcommands share the same config resolution (YAML → env → --set overrides).
``tui`` runs an in-process Textual front-end over :class:`ReplContext`.
``serve`` runs the stdio REPL server (transport over ReplContext).
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sys
import typing as t

import typer

from .config import BookScoutConfig

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="BookScout REPL — interactive agent runtime.",
)


def _parse_set_overrides(overrides: list[str]) -> dict[str, t.Any]:
    """Parse --set KEY=VALUE flags into a dict."""
    result: dict[str, t.Any] = {}
    for item in overrides:
        if "=" not in item:
            continue
        key, _, value = item.partition("=")
        key = key.strip()
        value = value.strip()
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = value
        result[key] = parsed
    return result


def _resolve_config(
    config_file: pathlib.Path | None,
    set_overrides: list[str] | None,
    data_dir: str | None,
    log_level: str | None,
) -> BookScoutConfig:
    """Resolve the BookScoutConfig from CLI flags + YAML + env."""
    if config_file is None:
        default_yaml = pathlib.Path("config.yaml")
        if default_yaml.exists():
            config_file = default_yaml

    bs_config = BookScoutConfig.from_yaml(config_file) if config_file is not None else BookScoutConfig()

    overrides = _parse_set_overrides(set_overrides or [])
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if log_level is not None:
        overrides["logging.level"] = log_level

    if overrides:
        bs_config = bs_config.with_overrides(overrides)

    bs_config.apply_env_vars()
    return bs_config


def _daemonize() -> int:
    """Daemonize the process. Returns the PID of the daemon."""
    if sys.platform == "win32":
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        return os.getpid()

    pid = os.fork()  # type: ignore[unreachable]
    if pid > 0:  # type: ignore[unreachable]
        return pid  # type: ignore[unreachable]

    os.setsid()  # type: ignore[unreachable]

    pid = os.fork()  # type: ignore[unreachable]
    if pid > 0:  # type: ignore[unreachable]
        sys.exit(0)  # type: ignore[unreachable]

    devnull = os.open(os.devnull, os.O_RDWR)  # type: ignore[unreachable]
    os.dup2(devnull, 0)  # type: ignore[unreachable]
    os.dup2(devnull, 1)  # type: ignore[unreachable]
    os.dup2(devnull, 2)  # type: ignore[unreachable]

    return os.getpid()  # type: ignore[unreachable]


@app.command()
def tui(
    config_file: t.Annotated[
        pathlib.Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to YAML config file. Default: ./config.yaml if it exists.",
        ),
    ] = None,
    set_overrides: t.Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            "-s",
            help="Override config value (dotted path, e.g. chatmodel.model=deepseek-v4-pro). Repeatable.",
        ),
    ] = None,
    data_dir: t.Annotated[
        str | None,
        typer.Option("--data-dir", help="Override data directory."),
    ] = None,
    log_level: t.Annotated[
        str | None,
        typer.Option("--log-level", "-l", help="Override log level (DEBUG, INFO, WARNING, ERROR)."),
    ] = None,
    book_id: t.Annotated[
        str | None,
        typer.Option(
            "--book",
            "-b",
            help="Skip the selector and open a specific book id directly.",
        ),
    ] = None,
) -> None:
    """Launch the interactive Textual TUI."""
    bs_config = _resolve_config(config_file, set_overrides, data_dir, log_level)
    import os

    from .tui import BookScoutTui

    tui_app = BookScoutTui(bs_config, initial_book_id=book_id)
    with contextlib.suppress(KeyboardInterrupt):
        tui_app.run()
    # Force-kill — loguru/asyncio threads keep the process alive
    # after Textual's event loop exits.
    os._exit(0)


@app.command()
def serve(
    config_file: t.Annotated[
        pathlib.Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to YAML config file. Default: ./config.yaml if it exists.",
        ),
    ] = None,
    set_overrides: t.Annotated[
        list[str] | None,
        typer.Option(
            "--set",
            "-s",
            help="Override config value (dotted path, e.g. chatmodel.model=deepseek-v4-pro). Repeatable.",
        ),
    ] = None,
    data_dir: t.Annotated[
        str | None,
        typer.Option("--data-dir", help="Override data directory."),
    ] = None,
    log_level: t.Annotated[
        str | None,
        typer.Option("--log-level", "-l", help="Override log level (DEBUG, INFO, WARNING, ERROR)."),
    ] = None,
    daemon: t.Annotated[
        bool,
        typer.Option(
            "--daemon",
            "-d",
            help="Run as a daemon (detach, print PID, no stdout/stderr logging).",
        ),
    ] = False,
) -> None:
    """Run the stdio REPL server (transport over ReplContext)."""
    import asyncio

    bs_config = _resolve_config(config_file, set_overrides, data_dir, log_level)

    if daemon:
        bs_config = bs_config.with_overrides({
            "logging.targets": [{"dest": "file", "level": "DEBUG", "pretty": True}],
        })
        log_file = bs_config.resolve_log_file_path()
        bs_config = bs_config.with_overrides({"logging.file": str(log_file)})

        pid = _daemonize()
        print(f"BookScout REPL daemon started (PID: {pid})")
        print(f"Log file: {log_file}")
        sys.stdout.flush()

    from .server import ReplServer

    server = ReplServer(config=bs_config)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_server(server))


async def _run_server(server: t.Any) -> None:
    await server.startup()
    try:
        await server.run()
    finally:
        await server.shutdown()


def cli_main() -> None:
    """Entry point for the bookscout-repl script."""
    app()


if __name__ == "__main__":
    cli_main()
