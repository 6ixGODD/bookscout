"""Configuration for the BookScout MCP Server.

Uses pydantic-settings to read from environment variables / .env file.
A YAML config file can be loaded as a lower-priority override.
"""

from __future__ import annotations

import pathlib
import typing as t

from pydantic import BaseModel
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class LLMSettings(BaseModel):
    """LLM (DeepSeek) settings."""

    api_key: str = Field(default="", description="DeepSeek API key")
    base_url: str = Field(default="https://api.deepseek.com", description="DeepSeek OpenAI-compatible base URL")
    model: str = Field(default="deepseek-chat", description="LLM model name")


class EmbeddingSettings(BaseModel):
    """Embedding (DashScope) settings."""

    api_key: str = Field(default="", description="DashScope embedding API key")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        description="DashScope OpenAI-compatible base URL",
    )
    model: str = Field(default="text-embedding-v4", description="Embedding model name")
    batch_size: int = Field(default=10, description="Batch size for embedding API (DashScope max 10)")


class MinerUSettings(BaseModel):
    """MinerU API settings."""

    api_token: str = Field(default="", description="MinerU API token")


class ServerSettings(BaseModel):
    """MCP server network settings."""

    host: str = Field(default="0.0.0.0", description="Bind host")
    port: int = Field(default=8765, description="Bind port")


class McpServerConfig(BaseSettings):
    """Top-level configuration for the BookScout MCP Server.

    Reads from environment variables (prefix ``BOOKSCOUT_``) and ``.env`` file.
    A YAML file can be loaded via :meth:`from_yaml` as a lower-priority base.

    Attributes:
        data_dir: Base directory for all book data.
        llm: LLM settings.
        embedding: Embedding settings.
        mineru: MinerU settings.
        server: Server network settings.
    """

    model_config = SettingsConfigDict(
        env_prefix="BOOKSCOUT_",
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    data_dir: pathlib.Path = Field(
        default=pathlib.Path("data"),
        description="Base directory for book data, workspaces, and indexes.",
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    mineru: MinerUSettings = Field(default_factory=MinerUSettings)
    server: ServerSettings = Field(default_factory=ServerSettings)

    # Flat env-var aliases (without nested prefix) for common keys.
    # These map to the nested settings for convenience.
    deepseek_api_key: str = Field(default="", alias="DEEPSEEK_API_KEY")
    deepseek_openai_base_url: str = Field(default="https://api.deepseek.com", alias="DEEPSEEK_OPENAI_BASE_URL")
    dashscope_embedding_api_key: str = Field(default="", alias="DASHSCOPE_EMBEDDING_API_KEY")
    dashscope_embedding_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        alias="DASHSCOPE_EMBEDDING_BASE_URL",
    )
    dashscope_embedding_model: str = Field(default="text-embedding-v4", alias="DASHSCOPE_EMBEDDING_MODEL")
    mineru_api_token: str = Field(default="", alias="MINERU_API_TOKEN")

    def model_post_init(self, __context: t.Any, /) -> None:  # pylint: disable=arguments-differ
        """Merge flat env vars into nested settings."""
        if self.deepseek_api_key and not self.llm.api_key:  # pylint: disable=no-member
            self.llm.api_key = self.deepseek_api_key
        if self.deepseek_openai_base_url and self.llm.base_url == "https://api.deepseek.com":  # pylint: disable=no-member
            self.llm.base_url = self.deepseek_openai_base_url
        if self.dashscope_embedding_api_key and not self.embedding.api_key:  # pylint: disable=no-member
            self.embedding.api_key = self.dashscope_embedding_api_key
        if (
            self.dashscope_embedding_base_url
            and self.embedding.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"  # pylint: disable=no-member
        ):
            self.embedding.base_url = self.dashscope_embedding_base_url
        if self.dashscope_embedding_model and self.embedding.model == "text-embedding-v4":  # pylint: disable=no-member
            self.embedding.model = self.dashscope_embedding_model
        if self.mineru_api_token and not self.mineru.api_token:  # pylint: disable=no-member
            self.mineru.api_token = self.mineru_api_token

    @classmethod
    def from_yaml(cls, path: pathlib.Path | str) -> McpServerConfig:
        """Load config from a YAML file, then override with env vars.

        YAML provides defaults; environment variables take priority.

        Args:
            path: Path to the YAML config file.

        Returns:
            A :class:`McpServerConfig` instance.
        """
        import yaml

        yaml_path = pathlib.Path(path)
        yaml_data: dict[str, t.Any] = {}
        if yaml_path.exists():
            with yaml_path.open(encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}

        # Create instance from YAML data, then let env vars override.
        return cls(**yaml_data)


__all__ = [
    "EmbeddingSettings",
    "LLMSettings",
    "McpServerConfig",
    "MinerUSettings",
    "ServerSettings",
]
