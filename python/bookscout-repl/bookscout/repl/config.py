"""BookScout REPL configuration — pydantic-settings based, with YAML + env + CLI override.

Configuration priority (highest to lowest):
    1. CLI flags (--set key=value)
    2. YAML file (--config path/to/config.yaml)
    3. Environment variables (BOOKSCOUT_* prefix, loaded from .env)
    4. Default values (all fields have defaults — BookScoutConfig() never raises)
"""

from __future__ import annotations

import os
import pathlib
import typing as t

from pydantic import BaseModel
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class ChatModelConfig(BaseModel):
    """LLM (ChatModel) configuration."""

    api_key: str = Field(default="", description="LLM API key")
    base_url: str = Field(default="https://api.deepseek.com", description="LLM API base URL (OpenAI-compatible)")
    model: str = Field(default="deepseek-v4-flash", description="LLM model name")
    stateless: bool = Field(default=True, description="Whether the ChatModel is stateless")


class EmbeddingConfig(BaseModel):
    """Embedding system configuration."""

    api_key: str = Field(default="", description="Embedding API key")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="Embedding API base URL",
    )
    model: str = Field(default="text-embedding-v4", description="Embedding model name")
    batch_size: int = Field(default=10, description="Batch size for embedding API calls")


class MinerUConfig(BaseModel):
    """MinerU PDF parsing API configuration."""

    api_token: str = Field(default="", description="MinerU API token")


class LoggingTargetConfig(BaseModel):
    """A single logging target (stdout, stderr, or file)."""

    dest: str = Field(default="stderr", description="Destination: stdout, stderr, or a file path")
    level: str = Field(default="INFO", description="Log level for this target")
    pretty: bool = Field(default=True, description="Pretty-print (colored) output")


class McpServerConfig(BaseModel):
    """External MCP server definition."""

    name: str = Field(..., description="Display name for this MCP server")
    url: str | None = Field(default=None, description="Streamable HTTP endpoint URL")
    command: str | None = Field(default=None, description="Command to spawn (stdio transport)")
    args: list[str] = Field(default_factory=list, description="Arguments for command")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for command")


class SkillConfig(BaseModel):
    """User-defined skill definition."""

    name: str = Field(..., description="Skill identifier")
    path: str = Field(..., description="Path to skill .md file, relative to workdir/skills/")
    description: str = Field(default="", description="What this skill does — shown to agent")


class LoggingConfigSection(BaseModel):
    """Logging configuration for the REPL server."""

    level: str = Field(default="INFO", description="Global minimum log level")
    targets: list[LoggingTargetConfig] = Field(
        default_factory=lambda: [
            LoggingTargetConfig(dest="stderr", level="INFO", pretty=True),
        ],
        description="Log targets (stdout/stderr/file)",
    )
    file: str = Field(default="logs/repl.log", description="Default log file path (relative to data_dir)")
    file_level: str = Field(default="DEBUG", description="Log level for the file target")


def _is_non_default(cls: type[BaseModel], key: str, value: t.Any) -> bool:
    """Check if a field value differs from its default."""
    field = cls.model_fields.get(key)
    if field is None:
        return True
    default = field.default
    if callable(default):
        default = default()
    return value != default


def _apply_env_overrides(cls: type[BookScoutConfig], data: dict[str, t.Any], env: dict[str, str]) -> None:
    """Recursively apply BOOKSCOUT_* env overrides on top of YAML-loaded data."""
    prefix = "BOOKSCOUT_"
    for field_name, field_info in cls.model_fields.items():
        env_prefix = f"{prefix}{field_name.upper()}"
        # Check nested models.
        ann = field_info.annotation
        if ann and hasattr(ann, "model_fields"):
            # Nested BaseModel — recurse.
            nested_data = data.get(field_name)
            if nested_data is None:
                nested_data = {}
                data[field_name] = nested_data
            if isinstance(nested_data, dict):
                _apply_nested_env(ann, nested_data, f"{env_prefix}__", env)
        else:
            # Simple field — check direct env var.
            val = env.get(env_prefix)
            if val is not None:
                # Try to parse as JSON for bools/ints.
                import json

                try:
                    parsed = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    parsed = val
                data[field_name] = parsed


def _apply_nested_env(model_cls: type[BaseModel], data: dict[str, t.Any], prefix: str, env: dict[str, str]) -> None:
    """Apply env overrides for a nested BaseModel."""
    for field_name in model_cls.model_fields:
        env_key = f"{prefix}{field_name.upper()}"
        val = env.get(env_key)
        if val is not None:
            import json

            try:
                parsed = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                parsed = val
            data[field_name] = parsed


class BookScoutConfig(BaseSettings):
    """Top-level BookScout REPL configuration.

    All fields have defaults — ``BookScoutConfig()`` never raises.
    Missing API keys surface as errors when components are initialized.
    """

    model_config = SettingsConfigDict(
        env_prefix="BOOKSCOUT_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    workdir: str = Field(
        default=str(pathlib.Path.home() / ".bookscout"),
        description="Root workdir — everything lives here (config, data, sessions, skills, logs).",
    )

    data_dir: str = Field(
        default="",  # empty means "{workdir}/data"
        description="Base directory for book data, workspaces, and indexes. Defaults to {workdir}/data.",
    )

    chatmodel: ChatModelConfig = Field(default_factory=ChatModelConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    mineru: MinerUConfig = Field(default_factory=MinerUConfig)
    logging: LoggingConfigSection = Field(default_factory=LoggingConfigSection)
    mcp_servers: list[McpServerConfig] = Field(
        default_factory=list,
        description="External MCP servers to connect at startup.",
    )
    skills: list[SkillConfig] = Field(
        default_factory=list,
        description="User-defined skills available to the agent.",
    )

    @property
    def resolved_data_dir(self) -> pathlib.Path:
        """Resolve data_dir, defaulting to {workdir}/data."""
        if self.data_dir:
            return pathlib.Path(self.data_dir)
        return pathlib.Path(self.workdir) / "data"

    @property
    def resolved_workdir(self) -> pathlib.Path:
        return pathlib.Path(self.workdir).resolve()

    @classmethod
    def from_yaml(cls, file: str | os.PathLike[str]) -> BookScoutConfig:
        """Load config from a YAML file, then let env vars override.

        Priority: env vars > YAML > defaults.

        The YAML may contain an ``env`` section that sets environment
        variables at load time (like Claude Code's env loading).
        """
        import yaml

        yaml_path = pathlib.Path(file)
        yaml_data: dict[str, t.Any] = {}
        if yaml_path.exists():
            with yaml_path.open(encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}

        env_section = yaml_data.pop("env", None)
        if isinstance(env_section, dict):
            for key, value in env_section.items():
                os.environ.setdefault(str(key), str(value))

        # First, load .env into os.environ (if not already loaded).
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass

        # Trigger pydantic-settings env var reading.
        cls()

        # Start with YAML values as base, then apply env overrides on top.
        valid_fields = set(cls.model_fields.keys())
        filtered = {k: v for k, v in yaml_data.items() if k in valid_fields}

        # Create from YAML first.
        yaml_instance = cls(**filtered)

        # Then check env vars and override where set.
        # pydantic-settings already loaded .env into os.environ.
        # But the YAML instance didn't read env because we passed kwargs.
        # So we need to manually check env for each nested field.
        result_data = yaml_instance.model_dump()
        _apply_env_overrides(cls, result_data, os.environ)
        return cls(**result_data)

    def with_overrides(self, overrides: dict[str, t.Any]) -> BookScoutConfig:
        """Apply dotted-path overrides (from CLI --set flags)."""
        data = self.model_dump()
        for dotted_key, value in overrides.items():
            parts = dotted_key.split(".")
            current = data
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
            current[parts[-1]] = value
        return BookScoutConfig(**data)

    def apply_env_vars(self) -> None:
        """Sync config values into os.environ for components that read them directly."""
        if self.mineru.api_token:  # pylint: disable=no-member
            os.environ["MINERU_API_TOKEN"] = self.mineru.api_token  # pylint: disable=no-member

    def resolve_log_file_path(self) -> pathlib.Path:
        """Resolve the log file path relative to data_dir."""
        log_file = pathlib.Path(self.logging.file)  # pylint: disable=no-member
        if not log_file.is_absolute():
            log_file = pathlib.Path(self.data_dir) / log_file
        log_file.parent.mkdir(parents=True, exist_ok=True)
        return log_file


__all__ = [
    "BookScoutConfig",
    "ChatModelConfig",
    "EmbeddingConfig",
    "LoggingConfigSection",
    "LoggingTargetConfig",
    "McpServerConfig",
    "MinerUConfig",
    "SkillConfig",
]
