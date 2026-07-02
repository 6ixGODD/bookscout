"""Console utilities for formatted output and user interaction.

Provides functions for printing messages with different levels (info, warning,
error, success), displaying progress spinners, prompting for user input, and
formatting various types of data for console output.
"""

from __future__ import annotations

import contextlib
import os
import pathlib
import sys
import textwrap
import time
import typing as t

import colorama
from colorama import Fore
from colorama import Style
import halo
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

colorama.init(strip=None)

_COLOR_ENABLED: bool = True
_VERBOSE: bool = False

spinner = halo.Halo(spinner="dots")
rich_console = Console()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and bool(sys.stdout.isatty())


def _colored(text: str, *codes: str) -> str:
    """Wrap text with ANSI color codes and a reset suffix."""
    if not _COLOR_ENABLED:
        return text
    return "".join(codes) + text + Style.RESET_ALL


# ---------------------------------------------------------------------------
# Formatting helpers (return strings, do not print)
# ---------------------------------------------------------------------------


def format_success(text: str, /) -> str:
    """Format text as success (green)."""
    return _colored(text, Fore.GREEN)


def format_error(text: str, /) -> str:
    """Format text as error (red)."""
    return _colored(text, Fore.RED)


def format_warning(text: str, /) -> str:
    """Format text as warning (yellow)."""
    return _colored(text, Fore.YELLOW)


def format_info(text: str, /) -> str:
    """Format text as info (blue)."""
    return _colored(text, Fore.BLUE)


def format_dim(text: str, /) -> str:
    """Format text as dimmed."""
    return _colored(text, Style.DIM)


def format_bold(text: str, /) -> str:
    """Format text as bold."""
    return _colored(text, Style.BRIGHT)


def format_italic(text: str, /) -> str:
    """Format text as italic."""
    return _colored(text, "\x1b[3m")


def format_underline(text: str, /) -> str:
    """Format text as underlined."""
    return _colored(text, "\x1b[4m")


def format_path(path: str | pathlib.Path, /) -> str:
    """Format a file path with colour."""
    return _colored(str(path), Fore.CYAN, Style.BRIGHT)


def format_command(command: str, /) -> str:
    """Format a command with color."""
    return _colored(command, Fore.CYAN, Style.BRIGHT)


def format_key(key: str, /) -> str:
    """Format a key/identifier with color."""
    return _colored(key, Fore.YELLOW)


def format_code(text: str, /) -> str:
    """Format inline code."""
    return _colored(text, Fore.MAGENTA)


def format_value(value: str, /) -> str:
    """Format a value with color."""
    return _colored(value, Fore.GREEN)


def format_title(text: str, /) -> str:
    """Format text as title (bold green)."""
    return _colored(text, Fore.GREEN, Style.BRIGHT)


def format_debug(text: str, /) -> str:
    """Format text as debug (dimmed)."""
    return _colored(text, Style.DIM)


def format_white(text: str, /) -> str:
    """Format text as white."""
    return _colored(text, Fore.WHITE)


def format_gray(text: str, /) -> str:
    """Format text as gray."""
    return _colored(text, Fore.LIGHTBLACK_EX)


def format_cyan(text: str, /) -> str:
    """Format text as cyan."""
    return _colored(text, Fore.CYAN)


# ---------------------------------------------------------------------------
# Print functions
# ---------------------------------------------------------------------------


def success(message: str, /, prefix: str = "✓ ") -> None:
    """Print a success message."""
    print(f"{format_success(prefix)}{format_success(message)}")


def error(message: str, /, prefix: str = "✗ ") -> None:
    """Print an error message."""
    print(f"{format_error(prefix)}{format_error(message)}", file=sys.stderr)


def warning(message: str, /, prefix: str = "! ") -> None:
    """Print a warning message."""
    print(f"{format_warning(prefix)}{format_warning(message)}")


def info(message: str) -> None:
    """Print an info message."""
    print(f"{message}")


def debug(message: str, /, prefix: str = "") -> None:
    """Print a debug message (dimmed). Only shown in verbose mode.

    Debug messages are only displayed when --verbose flag is set.

    Args:
        message: The debug message to print.
        prefix: Optional prefix (default: empty).
    """
    if _VERBOSE:
        print(format_dim(f"{prefix}{message}"))


def step(message: str, /, step: int | None = None) -> None:
    """Print a step message in a process."""
    if step is not None:
        prefix = _colored(f"[{step}]", Fore.CYAN, Style.BRIGHT)
        print(f"\n{prefix} {message}")
    else:
        print(f"\n{format_cyan('*')} {message}")


def path(
    path: str | pathlib.Path | os.PathLike[str],
    /,
    label: str | None = None,
    exists: bool | None = None,
) -> None:
    """Print a formatted file path."""
    path = pathlib.Path(path)

    parts = []
    if label:
        parts.append(format_bold(f"{label}:"))

    if exists is not None:
        indicator = format_success("✓") if exists else format_error("✗")
        parts.append(indicator)

    parts.append(format_cyan(str(path)))
    print(" ".join(parts))


def command(cmd: str, /) -> None:
    """Print a command being executed."""
    print(format_dim(f"  $ {cmd}"))


def header(text: str, /) -> None:
    """Print a section header."""
    print()
    print(format_bold(text))
    print(format_gray("─" * len(text)))


def separator(char: str = "─", length: int = 60) -> None:
    """Print a separator line."""
    print(format_gray(char * length))


def key_value(
    data: dict[str, t.Any],
    /,
    indent: int = 0,
) -> None:
    """Print key-value pairs in a clean format."""
    if not data:
        return

    max_key_len = max(len(str(k)) for k in data)
    indent_str = "  " * indent

    for key, value in data.items():
        key_str = format_bold(str(key).ljust(max_key_len))
        value_str = str(value)
        print(f"{indent_str}{key_str}    {value_str}")


def list_items(
    items: list[str],
    /,
    bullet: str = "•",
    indent: int = 0,
) -> None:
    """Print a bulleted list of items."""
    indent_str = "  " * indent
    for item in items:
        print(f"{indent_str}{format_cyan(bullet)} {item}")


@contextlib.contextmanager
def loading(
    text: str = "Loading",
    /,
    success_text: str | None = None,
    error_text: str | None = None,
) -> t.Generator[halo.Halo]:
    """Context manager for showing a loading spinner."""
    spinner.text = text
    spinner.start()

    try:
        yield spinner
        if success_text:
            spinner.succeed(success_text)
        else:
            spinner.succeed()
    except Exception as e:
        if error_text:
            spinner.fail(error_text)
        else:
            spinner.fail(f"{text} failed: {e}")
        raise
    finally:
        spinner.stop()


@contextlib.contextmanager
def section(title: str, /) -> t.Generator[None]:
    """Context manager for a named section."""
    print()
    print(_colored(f"┌─ {title}", Fore.CYAN, Style.BRIGHT))

    try:
        yield
    finally:
        print(_colored("└─" + "─" * (len(title) + 2), Fore.CYAN))


class TaskProgress:
    """Generic task progress tracker with animated spinner.

    Can be used for any long-running tasks: building, compiling, downloading,
    installing, etc.

    Shows:
        - Line 1: Spinner + task description
        - Line 2: Current item [idx/total] item_name
        - Line 3+: Scrolling logs (last N lines)
    """

    def __init__(self, total: int, task_description: str = "Processing", max_log_lines: int = 20):
        self.total = total
        self.current = 0
        self.current_item = ""
        self.task_description = task_description
        self.success_count = 0
        self.failed_count = 0
        self.start_time = 0.0
        self.max_log_lines = max_log_lines

        # Logs
        self.logs: list[str] = []

        # Completed items
        self.completed: list[str] = []

        # Rich Live
        self.live: Live | None = None

    def _create_display(self) -> Table:
        """Create the display with animated spinner."""
        table = Table.grid(padding=0)
        table.add_column()

        # Line 1: Spinner + task description
        spinner_row = Table.grid(padding=0)
        spinner_row.add_column(width=2)
        spinner_row.add_column()
        spinner_row.add_row(Spinner("dots", style="cyan"), Text(f"{self.task_description}...", style="cyan"))
        table.add_row(spinner_row)

        # Line 2: Current item
        if self.current > 0:
            line2 = Text()
            line2.append(f"[{self.current}/{self.total}] ", style="dim")
            line2.append(self.current_item, style="bold cyan")
            table.add_row(line2)

        # Line 3+: Recent logs
        for log in self.logs[-self.max_log_lines :]:
            table.add_row(Text(f"  {log}", style="dim"))

        return table

    def start(self) -> None:
        """Start the task progress."""
        self.start_time = time.time()

        self.live = Live(
            self._create_display(),
            console=rich_console,
            refresh_per_second=10,
            transient=False,
        )
        self.live.start()

    def update(self, item_name: str, index: int) -> None:
        """Update current item being processed."""
        self.current = index
        self.current_item = item_name
        self.logs.clear()

        if self.live:
            self.live.update(self._create_display())

    def add_log(self, log_line: str) -> None:
        """Add a log line."""
        # Split multi-line logs
        for line in log_line.strip().split("\n"):
            if line.strip():
                self.logs.append(line.strip())

        if self.live:
            self.live.update(self._create_display())

    def complete(self, item_name: str, success: bool) -> None:
        """Mark an item as completed."""
        if success:
            self.success_count += 1
            icon = "✓"
            style = "green"
        else:
            self.failed_count += 1
            icon = "✗"
            style = "red"

        self.completed.append(f"[{style}]{icon}[/{style}] [dim]{item_name}[/dim]")
        self.logs.clear()

        if self.live:
            self.live.update(self._create_display())

    def stop(self) -> None:
        """Stop and show summary."""
        if self.live:
            self.live.stop()

        # Print all completed items
        print()
        for completed_line in self.completed:
            rich_console.print(completed_line)

        # Final summary
        elapsed = time.time() - self.start_time
        print()

        if self.failed_count == 0:
            rich_console.print(
                f"[green]✓[/green] Completed [bold]{self.success_count}[/bold] items in [dim]{elapsed:.1f}s[/dim]"
            )
        else:
            rich_console.print(
                f"[yellow]![/yellow] Completed [bold]{self.success_count}[/bold] items, "
                f"[red]{self.failed_count} failed[/red] "
                f"in [dim]{elapsed:.1f}s[/dim]"
            )

    def __enter__(self) -> t.Self:
        self.start()
        return self

    def __exit__(self, exc_type: t.Any, exc_val: t.Any, exc_tb: t.Any) -> None:
        self.stop()


def banner(text: str, /, subtitle: str | None = None, version: str | None = None) -> None:
    """Print an application banner."""
    width = max(len(text), len(subtitle) if subtitle else 0) + 4

    print()
    print(_colored("┌" + "─" * (width - 2) + "┐", Fore.CYAN, Style.BRIGHT))
    print(_colored(f"│ {text.center(width - 4)} │", Fore.CYAN, Style.BRIGHT))

    if subtitle:
        print(_colored(f"│ {subtitle.center(width - 4)} │", Fore.CYAN))

    if version:
        version_text = f"v{version}"
        print(_colored(f"│ {version_text.center(width - 4)} │", Style.DIM))

    print(_colored("└" + "─" * (width - 2) + "┘", Fore.CYAN, Style.BRIGHT))
    print()


def confirm(prompt: str, /, default: bool = False) -> bool:
    """Ask for user confirmation with a yes/no prompt."""
    suffix = format_dim("[Y/n]" if default else "[y/N]")
    response = input(f"{prompt} {suffix} ").strip().lower()

    if not response:
        return default

    if response in ("y", "yes"):
        return True
    if response in ("n", "no"):
        return False

    error("Please answer 'y' or 'n'")
    return confirm(prompt, default)


def prompt_input(message: str, /, default: str = "", prefix: str = "") -> str:
    """Prompt user for input.

    Args:
        message: The prompt text (e.g. ``"Package name"``).
        default: Default value shown in brackets if user enters nothing.
        prefix:  A dimmed prefix shown right before the cursor (e.g.
                 ``"bookscout-"``).  The returned value already has the
                 prefix prepended — the user only types the suffix.
    """
    # Build the prompt line piece by piece
    parts = [f"{message}: "]
    if prefix:
        parts.append(format_dim(prefix))
    if default:
        parts.append(f" {format_dim(f'[{default}]')}")
    parts.append("")

    sys.stdout.write("".join(parts))
    sys.stdout.flush()
    raw = input().strip()
    if not raw:
        return prefix + default if prefix else default
    return prefix + raw


def choose(prompt: str, choices: list[str], /, default: int = 0) -> int:
    """Present a numbered menu and return the index of the chosen item.

    Args:
        prompt:  Question shown above the menu.
        choices: List of option strings.
        default: 0-based index of the pre-selected option.

    Returns:
        0-based index of the selected choice.
    """
    print(f"{format_bold(prompt)}")
    for i, choice in enumerate(choices):
        marker = format_cyan("❯") if i == default else " "
        num = format_dim(f"{i + 1}.")
        print(f"  {marker} {num} {choice}")
    print()

    hint = format_dim(f"[1-{len(choices)}, default {default + 1}]")
    while True:
        raw = input(f"Choice {hint}: ").strip()
        if not raw:
            return default
        try:
            idx = int(raw) - 1
        except ValueError:
            error("Please enter a number.")
            continue
        if 0 <= idx < len(choices):
            return idx
        error(f"Please enter a number between 1 and {len(choices)}.")

    return default  # Fallback, should never reach here


def exception_detail(exc: Exception, /, show_traceback: bool = False) -> None:
    """Display detailed exception information."""
    print()
    print(format_error("┌" + "─" * 68 + "┐"))
    print(format_error(f"│ ERROR: {type(exc).__name__}".ljust(70) + "│"))
    print(format_error("├" + "─" * 68 + "┤"))

    # Wrap error message
    msg = str(exc)
    for line in textwrap.wrap(msg, width=66):
        print(format_error(f"│ {line}".ljust(70) + "│"))

    print(format_error("└" + "─" * 68 + "┘"))

    if show_traceback:
        print()
        print(format_dim("Traceback:"))
        print(format_gray("─" * 70))
        import traceback

        traceback.print_exception(type(exc), exc, exc.__traceback__)


def error_summary(
    title: str,
    /,
    details: dict[str, str] | None = None,
    suggestions: list[str] | None = None,
) -> None:
    """Display an error summary with optional details and suggestions."""
    print()
    print(format_error(f"✗ {title}"))
    print()

    if details:
        print(format_bold("Details:"))
        key_value(details, indent=1)
        print()

    if suggestions:
        print(format_bold("Suggestions:"))
        list_items(suggestions, bullet="→", indent=1)
        print()


def fatal_error(message: str, /, exit_code: int = 1) -> t.NoReturn:
    """Display a fatal error and exit."""
    print()
    print(format_error("╔" + "═" * 68 + "╗"))
    print(format_error("║" + " FATAL ERROR ".center(68) + "║"))
    print(format_error("╠" + "═" * 68 + "╣"))

    for line in textwrap.wrap(message, width=66):
        print(format_error("║ " + line.ljust(67) + "║"))

    print(format_error("╚" + "═" * 68 + "╝"))
    print()
    sys.exit(exit_code)


def show_error(exc: Exception, /, verbose: bool = False) -> None:
    """Display error information."""
    error(f"{type(exc).__name__}: {exc}")

    if verbose:
        print()
        print(format_dim("Traceback:"))
        import traceback

        traceback.print_exception(type(exc), exc, exc.__traceback__)


def table_dict(
    data: dict[str, t.Any],
    /,
) -> None:
    """Print a simple table from a dictionary."""
    if not data:
        return

    max_key_len = max(len(str(k)) for k in data)

    for key, value in data.items():
        key_str = format_bold(str(key).ljust(max_key_len))
        print(f"  {key_str}  {value}")


# ---------------------------------------------------------------------------
# File/directory utilities
# ---------------------------------------------------------------------------


def ensure_dir(directory: str | pathlib.Path, /) -> pathlib.Path:
    """Ensure a directory exists, create if it doesn't."""
    path = pathlib.Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return path


def check_file_exists(filepath: pathlib.Path, /, force: bool = False) -> bool:
    """Check if file exists and handle accordingly.

    Returns:
        True if should proceed with overwrite, False otherwise
    """
    if not filepath.exists():
        return False

    if force:
        warning(f"Overwriting existing file: {filepath}")
        return True

    warning(f"File already exists: {filepath}")
    if confirm("Overwrite?", default=False):
        info("Overwriting file...")
        return True

    info("Skipping file generation")
    return False


def dir_exists(directory: str | pathlib.Path, /) -> bool:
    """Check if a directory exists."""
    return pathlib.Path(directory).is_dir()


def file_exists(filepath: str | pathlib.Path, /) -> bool:
    """Check if a file exists."""
    return pathlib.Path(filepath).is_file()


# ---------------------------------------------------------------------------
# Global state management
# ---------------------------------------------------------------------------


def init_ansi_formatter() -> None:
    """Initialize color output based on the environment."""
    global _COLOR_ENABLED
    _COLOR_ENABLED = _supports_color()
    if not _COLOR_ENABLED:
        colorama.init(strip=True)


def setup_quiet_mode() -> None:
    """Redirect stdout to devnull for quiet mode.

    In quiet mode, all output except errors (stderr) is suppressed.
    This is useful for scripts and automation where only error output
    is needed.

    Warning:
        This redirects sys.stdout globally and cannot be undone.
        Should only be called once at CLI startup.
    """
    sys.stdout = open(os.devnull, "w")  # noqa: PTH123, SIM115


def set_verbose_mode(enabled: bool | None = None) -> None:
    """Enable or disable verbose mode globally.

    When verbose mode is enabled:
        - debug() messages are shown
        - Tracebacks are printed on errors
        - Additional diagnostic info is displayed

    Args:
        enabled: True to enable, False to disable, None to toggle.

    Examples:
        ```python
        set_verbose_mode(True)  # Enable verbose output
        set_verbose_mode(False)  # Disable verbose output
        set_verbose_mode()  # Toggle current state
        ```
    """
    global _VERBOSE
    _VERBOSE = enabled if enabled is not None else not _VERBOSE
