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
"""Assembles the final system prompt from skills, SOUL, and base instructions."""

from __future__ import annotations

import pathlib
import datetime


class PromptBuilder:
    """Builds the system prompt with skills section, base instructions, SOUL, and date/time."""

    def __init__(
        self,
        skill_descriptions: list[dict[str, str]],
        soul_path: pathlib.Path,
        base_system_prompt: str,
    ) -> None:
        self._skill_descriptions = skill_descriptions
        self._soul_path = soul_path
        self._base_system_prompt = base_system_prompt

    def build(self) -> str:
        """Assemble the complete system prompt."""
        parts: list[str] = []

        # 1. Skills section
        if self._skill_descriptions:
            parts.append("## Available Skills")
            parts.append(
                "You have access to a `skill_fetch` tool. Call it with a skill name "
                "to load its full instructions when needed. Do NOT guess skill content — "
                "always fetch it first."
            )
            parts.append("")
            for skill in self._skill_descriptions:
                parts.append(f"- **{skill['name']}**: {skill['description']}")
            parts.append("")
            parts.append("---")
            parts.append("")

        # 2. Base system prompt
        parts.append(self._base_system_prompt)

        # 3. SOUL
        soul_content = self._read_soul()
        if soul_content:
            parts.append("")
            parts.append("---")
            parts.append("")
            parts.append(soul_content)

        # 4. Current date/time
        now = datetime.datetime.now().astimezone()
        tz_name = now.tzinfo.tzname(now) if now.tzinfo else "UTC"
        parts.append("")
        parts.append("---")
        parts.append("")
        parts.append(f"Current date: {now.strftime('%Y-%m-%d')}")
        parts.append(f"Current time: {now.strftime('%H:%M:%S')} {tz_name}")

        return "\n".join(parts)

    def _read_soul(self) -> str | None:
        """Read SOUL.md if it exists."""
        if self._soul_path.exists():
            return self._soul_path.read_text(encoding="utf-8").strip()
        return None
