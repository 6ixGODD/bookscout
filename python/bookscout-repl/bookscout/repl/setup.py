"""BookScout first-time setup wizard — a minimal Textual UI.

Steps:
  1. LLM provider config
  2. Embedding provider config
  3. MinerU PDF parser (optional)
  4. PATH setup
  5. Write config.yaml

Run once when ``~/.bookscout/config.yaml`` doesn't exist.
"""

from __future__ import annotations

import os
import pathlib
import sys
import typing as t

from textual.app import App
from textual.containers import Center
from textual.containers import Container
from textual.widgets import Input
from textual.widgets import Label
from textual.widgets import Static
import yaml

_BOOKSCOUT_BAT = """@echo off
cd /d "{project_root}"
uv run bookscout %*
"""


def _set_user_env(name: str, value: str) -> bool:
    """Try to set a user-level environment variable via registry.

    Returns True on success, False on failure (permission denied, etc.).
    """
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        )
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _add_to_path(entry: str) -> bool:
    """Add an entry to the user PATH.

    Returns True on success.
    """
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_SET_VALUE,
        ) as key:
            current, _ = winreg.QueryValueEx(key, "PATH")
            if entry not in current:
                new_path = f"{entry};{current}" if current else entry
                winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ, new_path)
        return True
    except OSError:
        return False


def _resolve_project_root() -> pathlib.Path:
    """Heuristic: walk up from this file to find pyproject.toml."""
    this_dir = pathlib.Path(__file__).resolve().parent
    for _ in range(6):
        if (this_dir / "pyproject.toml").exists():
            return this_dir
        this_dir = this_dir.parent
    return pathlib.Path.cwd()


# ---------------------------------------------------------------------------
# Setup wizard app
# ---------------------------------------------------------------------------


class SetupWizard(App[dict[str, t.Any] | None]):
    """Minimal Textual app that walks the user through configuration.

    Press Enter on the focused Input to advance.  Ctrl+C to bail.
    Exits with a dict of gathered values or ``None`` when the user cancels.
    """

    CSS = """
    Screen {
        background: #000000;
        color: #c0c0c0;
    }
    #welcome {
        height: 1fr;
        content-align: center middle;
        text-align: center;
        color: #ffffff;
    }
    #welcome_title {
        color: #ffffff;
        text-style: bold;
    }
    #welcome_sub {
        color: #888888;
        margin: 1 0;
    }
    Center {
        height: 1fr;
    }
    #step_box {
        width: 60;
        height: auto;
        border: solid #555555;
        padding: 1 2;
    }
    #step_title {
        color: #ffffff;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    Label {
        color: #888888;
        height: 1;
    }
    Input {
        border: none;
        width: 1fr;
        color: #ffffff;
        background: #111111;
        height: 1;
        margin-bottom: 1;
    }
    Input:focus {
        border: none;
        background: #222222;
    }
    #error_line {
        color: #cc6666;
        height: 1;
    }
    #status_line {
        color: #666666;
        height: 1;
        text-align: center;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._values: dict[str, str] = {
            "llm_api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
            "llm_base_url": "https://api.deepseek.com",
            "llm_model": "deepseek-v4-flash",
            "emb_api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
            "emb_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "emb_model": "text-embedding-v4",
            "mineru_token": os.environ.get("MINERU_API_TOKEN", ""),
        }
        self._steps: list[str] = [
            "welcome",
            "llm",
            "embedding",
            "mineru",
            "path",
            "done",
        ]
        self._step_idx = 0

    def compose(self) -> None:
        yield Center(
            Static(
                "\n\nWelcome to BookScout\n\nPress Enter to configure your providers.\nCtrl+C to quit.",
                id="welcome",
            ),
            id="welcome_screen",
        )
        yield Container(id="step_screen")

    def on_mount(self) -> None:
        self._show_step()

    def _show_step(self) -> None:
        name = self._steps[self._step_idx]
        ws = self.query_one("#welcome_screen", Center)
        ss = self.query_one("#step_screen", Container)

        if name == "welcome":
            ws.display = True
            ss.display = False
        else:
            ws.display = False
            ss.display = True
            ss.remove_children()
            self._build_form(ss, name)

    def _build_form(self, container: Container, name: str) -> None:
        if name == "llm":
            title = "LLM Provider (ChatModel)"
            fields: list[tuple[str, str, str]] = [
                ("llm_api_key", "API key", self._values["llm_api_key"]),
                ("llm_base_url", "Base URL", self._values["llm_base_url"]),
                ("llm_model", "Model", self._values["llm_model"]),
            ]
        elif name == "embedding":
            title = "Embedding Provider"
            fields = [
                ("emb_api_key", "API key", self._values["emb_api_key"]),
                ("emb_base_url", "Base URL", self._values["emb_base_url"]),
                ("emb_model", "Model", self._values["emb_model"]),
            ]
        elif name == "mineru":
            title = "MinerU PDF Parser (optional)"
            fields = [
                ("mineru_token", "API token", self._values["mineru_token"]),
            ]
        elif name == "path":
            self._show_path_step(container)
            return
        elif name == "done":
            self._show_done(container)
            return
        else:
            return

        widgets: list[Static | Label | Input] = [Static(title, id="step_title")]
        for i, (key, label, default) in enumerate(fields):
            widgets.append(Label(label))
            inp = Input(value=default, id=f"inp_{key}")
            widgets.append(inp)
            if i == 0:
                self.set_timer(0.01, lambda i=inp: i.focus())
        widgets.append(Static("", id="error_line"))
        widgets.append(Static("Enter  next    Ctrl+C  quit", id="status_line"))

        box = Container(*widgets, id="step_box")
        container.mount(Center(box))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        key = event.input.id.replace("inp_", "")

        if key == "path":
            answer = event.value.strip().lower()
            project_root = pathlib.Path(self._project_root)
            if answer in ("yes", "y", ""):
                bat_path = project_root / "bookscout.bat"
                try:
                    bat_content = _BOOKSCOUT_BAT.format(project_root=project_root)
                    bat_path.write_text(bat_content, encoding="ascii")
                except OSError as e:
                    self.query_one("#path_result", Static).update(f"Could not write {bat_path}: {e}")
                    return

                env_ok = _set_user_env("BOOKSCOUT_PATH", str(project_root))
                path_ok = _add_to_path("%BOOKSCOUT_PATH%")

                msg_parts: list[str] = []
                if env_ok:
                    msg_parts.append("BOOKSCOUT_PATH set.")
                else:
                    msg_parts.append("Could not set BOOKSCOUT_PATH.  Run manually:")
                    msg_parts.append(f'  setx BOOKSCOUT_PATH "{project_root}"')
                if path_ok:
                    msg_parts.append("Added %BOOKSCOUT_PATH% to PATH.")
                else:
                    msg_parts.append("Could not update PATH.  Run manually:")
                    msg_parts.append('  setx PATH "%BOOKSCOUT_PATH%;%PATH%"')
                if env_ok and path_ok:
                    msg_parts.append("")
                    msg_parts.append("Restart your terminal for PATH to take effect.")
                self.query_one("#path_result", Static).update("\n".join(msg_parts))
            self._next_step()
            return

        if key.startswith("llm_") or key.startswith("emb_") or key == "mineru_token":
            self._values[key] = event.value.strip()
            inputs = self.query("#step_box Input")
            current_idx = next((i for i, w in enumerate(inputs) if w is event.input), -1)
            if current_idx >= 0 and current_idx < len(inputs) - 1:
                inputs[current_idx + 1].focus()
            else:
                self._next_step()
        else:
            self._next_step()

    def _next_step(self) -> None:
        self._step_idx += 1
        if self._step_idx >= len(self._steps):
            self.exit(self._values)
            return
        self._show_step()

    def _show_path_step(self, container: Container) -> None:
        project_root = _resolve_project_root()
        self._project_root = str(project_root)
        inp = Input(value="yes", id="inp_path")
        box = Container(
            Static("PATH setup", id="step_title"),
            Label(f"Project root: {project_root}"),
            Label(""),
            Label("Add %BOOKSCOUT_PATH% to your PATH?"),
            Label("This lets you type 'bookscout' from any terminal."),
            Static("", id="path_result"),
            Static("Enter  yes    y  skip", id="status_line"),
            inp,
            id="step_box",
        )
        container.mount(Center(box))
        self.set_timer(0.01, lambda: inp.focus())

    def _show_done(self, container: Container) -> None:
        box = Container(
            Static("Done!", id="step_title"),
            Label(""),
            Label("Config written. Press Enter to launch BookScout."),
            Static("", id="status_line"),
            id="step_box",
        )
        container.mount(Center(box))

    async def on_key(self, event: t.Any) -> None:
        if event.key == "enter":
            name = self._steps[self._step_idx]
            if name in ("welcome", "done"):
                self._next_step()
                return


def run_setup(config_path: pathlib.Path) -> pathlib.Path:
    """Run the Textual setup wizard, write config.yaml, return the path."""
    app = SetupWizard()
    values = app.run()
    if values is None:
        print("Setup cancelled.")
        sys.exit(0)

    config_data: dict[str, t.Any] = {
        "chatmodel": {
            "api_key": values.get("llm_api_key", ""),
            "base_url": values.get("llm_base_url", ""),
            "model": values.get("llm_model", ""),
            "stateless": False,
        },
        "embedding": {
            "api_key": values.get("emb_api_key", ""),
            "base_url": values.get("emb_base_url", ""),
            "model": values.get("emb_model", ""),
        },
        "mineru": {
            "api_token": values.get("mineru_token", ""),
        },
        "logging": {
            "level": "INFO",
            "targets": [
                {"dest": "stderr", "level": "INFO", "pretty": True},
            ],
            "file": "logs/repl.log",
            "file_level": "DEBUG",
        },
    }
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(config_data, f, allow_unicode=True, default_flow_style=False)
    return config_path


def run_setup_wizard(config_path: pathlib.Path) -> pathlib.Path:
    """Legacy wrapper — delegates to the Textual wizard."""
    return run_setup(config_path)


__all__ = ["run_setup", "run_setup_wizard"]
