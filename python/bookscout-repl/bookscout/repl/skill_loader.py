# Copyright 2026 BoChen SHEN
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Skill loader — reads skill configs, loads .md files, caches content."""

from __future__ import annotations

import pathlib
import typing as t

from .config import SkillConfig


class SkillLoader:
    """Loads skill definitions from config and fetches content from disk.

    Content is cached in memory after first fetch — skills are only read
    once per session.
    """

    def __init__(self, workdir: pathlib.Path, skills: list[SkillConfig]) -> None:
        self._workdir = workdir
        self._skills = skills
        self._cache: dict[str, str] = {}

    def list_skills(self) -> list[dict[str, str]]:
        """Return skill summaries (name + description) for system prompt."""
        return [
            {"name": s.name, "description": s.description}
            for s in self._skills
        ]

    def get_skill(self, name: str) -> str | None:
        """Get skill content by name. Returns None if not found."""
        if name in self._cache:
            return self._cache[name]

        cfg = next((s for s in self._skills if s.name == name), None)
        if cfg is None:
            return None

        skill_path = self._workdir / "skills" / cfg.path
        if not skill_path.exists():
            return None

        content = skill_path.read_text(encoding="utf-8")
        self._cache[name] = content
        return content
