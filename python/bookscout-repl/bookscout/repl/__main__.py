"""BookScout REPL CLI entry point — Typer-based launcher.

Subcommands::

    bookscout-repl tui   --config config.yaml --set chatmodel.model=...
    bookscout-repl serve --config config.yaml [--daemon]
    bookscout-repl ws    --config config.yaml --port 18732

Both subcommands share the same config resolution (YAML → env → --set overrides).
``tui`` runs an in-process Textual front-end over :class:`ReplContext`.
``serve`` runs the stdio REPL server (transport over ReplContext).
``ws`` runs the WebSocket server for the Electron desktop client.
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

_DEFAULT_WORKSPACE = pathlib.Path.home() / ".bookscout"


def _default_config_path() -> pathlib.Path:
    return _DEFAULT_WORKSPACE / "config.yaml"


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
    debug_file: pathlib.Path | None,
    workdir: str | None = None,
) -> BookScoutConfig:
    """Resolve the BookScoutConfig from CLI flags + YAML + env."""
    if config_file is None:
        config_file = _default_config_path()

    if not config_file.exists():
        from .setup import run_setup

        config_file = run_setup(config_file)

    bs_config = BookScoutConfig.from_yaml(config_file)

    overrides = _parse_set_overrides(set_overrides or [])
    if workdir is not None:
        overrides["workdir"] = workdir
    if data_dir is not None:
        overrides["data_dir"] = data_dir
    if log_level is not None and "logging.level" not in overrides:
        overrides["logging.level"] = log_level

    if overrides:
        bs_config = bs_config.with_overrides(overrides)

    if debug_file is not None:
        bs_config = _attach_debug_target(bs_config, debug_file)

    bs_config.apply_env_vars()
    return bs_config


def _attach_debug_target(bs_config: BookScoutConfig, debug_file: pathlib.Path) -> BookScoutConfig:
    """Validate and attach a debug-file log target."""
    debug_path = debug_file.resolve()
    if debug_path.exists():
        with debug_path.open("rb") as f:
            chunk = f.read(1024)
        if b"\x00" in chunk or not _is_text(chunk):
            raise typer.BadParameter(f"debug file is binary, not text: {debug_path}")
    else:
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.touch()
    targets = list(bs_config.logging.targets)
    from .config import LoggingTargetConfig

    targets.append(LoggingTargetConfig(dest=str(debug_path), level="DEBUG", pretty=False))
    return bs_config.with_overrides({"logging.targets": [t.model_dump() for t in targets]})


def _is_text(data: bytes) -> bool:
    try:
        data.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------


def _daemonize() -> int:
    """Daemonize the process. Returns the PID of the daemon."""
    if sys.platform == "win32":
        devnull = os.open(os.devnull, os.O_RDWR)
        os.dup2(devnull, 0)
        return os.getpid()

    # pylint: disable-next=no-member
    pid = os.fork()  # type: ignore[unreachable]
    if pid > 0:  # type: ignore[unreachable]
        return pid  # type: ignore[unreachable]

    # pylint: disable-next=no-member
    os.setsid()  # type: ignore[unreachable]

    # pylint: disable-next=no-member
    pid = os.fork()  # type: ignore[unreachable]
    if pid > 0:  # type: ignore[unreachable]
        sys.exit(0)  # type: ignore[unreachable]

    # pylint: disable-next=no-member
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
            help=f"Path to YAML config file. Default: {_default_config_path()}",
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
    workdir: t.Annotated[
        str | None,
        typer.Option("--workdir", "-w", help=f"Workdir root. Default: {_DEFAULT_WORKSPACE}"),
    ] = None,
    data_dir: t.Annotated[
        str | None,
        typer.Option("--data-dir", help=f"Override data directory. Default: {_DEFAULT_WORKSPACE}"),
    ] = None,
    log_level: t.Annotated[
        str | None,
        typer.Option("--log-level", "-l", help="Override log level (DEBUG, INFO, WARNING, ERROR)."),
    ] = None,
    debug_file: t.Annotated[
        pathlib.Path | None,
        typer.Option(
            "--debug",
            "-d",
            help="Append debug logs to a text file. Must be a valid text file (error if binary).",
        ),
    ] = None,
) -> None:
    """Launch the interactive Textual TUI."""
    bs_config = _resolve_config(config_file, set_overrides, data_dir, log_level, debug_file, workdir)
    from .tui_textual import BookScoutTui

    tui_app = BookScoutTui(bs_config)
    with contextlib.suppress(KeyboardInterrupt):
        tui_app.run()
    os._exit(0)


@app.command()
def serve(
    config_file: t.Annotated[
        pathlib.Path | None,
        typer.Option(
            "--config",
            "-c",
            help=f"Path to YAML config file. Default: {_default_config_path()}",
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
    workdir: t.Annotated[
        str | None,
        typer.Option("--workdir", "-w", help=f"Workdir root. Default: {_DEFAULT_WORKSPACE}"),
    ] = None,
    data_dir: t.Annotated[
        str | None,
        typer.Option("--data-dir", help=f"Override data directory. Default: {_DEFAULT_WORKSPACE}"),
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

    bs_config = _resolve_config(config_file, set_overrides, data_dir, log_level, None, workdir)

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


@app.command()
def ws(
    config_file: t.Annotated[
        pathlib.Path | None,
        typer.Option(
            "--config",
            "-c",
            help=f"Path to YAML config file. Default: {_default_config_path()}",
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
    workdir: t.Annotated[
        str | None,
        typer.Option("--workdir", "-w", help=f"Workdir root. Default: {_DEFAULT_WORKSPACE}"),
    ] = None,
    data_dir: t.Annotated[
        str | None,
        typer.Option("--data-dir", help=f"Override data directory. Default: {_DEFAULT_WORKSPACE}"),
    ] = None,
    log_level: t.Annotated[
        str | None,
        typer.Option("--log-level", "-l", help="Override log level (DEBUG, INFO, WARNING, ERROR)."),
    ] = None,
    port: t.Annotated[
        int,
        typer.Option("--port", "-p", help="WebSocket server port. Default: 18732."),
    ] = 18732,
) -> None:
    """Run the WebSocket REPL server for the Electron desktop client."""
    import asyncio

    from .ws_server import run_ws_server

    bs_config = _resolve_config(config_file, set_overrides, data_dir, log_level, None, workdir)

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_ws_server(bs_config, port=port))


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
