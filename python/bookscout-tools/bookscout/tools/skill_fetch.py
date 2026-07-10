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
"""On-demand skill content loading tool."""

from __future__ import annotations

import typing as t
from typing import Annotated

from bookscout.tools import BaseTool, Property


class SkillFetchTool(
    BaseTool,
    name="skill_fetch",
    description=(
        "Fetch the full content of a user-defined skill. "
        "Use this when you need detailed instructions for a specific skill. "
        "Call with the skill name to get its complete guidance."
    ),
):
    """Tool that loads skill content on demand, keeping it out of context until needed."""

    def __init__(self, skill_loader: t.Any) -> None:
        self._loader = skill_loader

    async def __call__(
        self,
        skill_name: Annotated[str, Property(description="Name of the skill to fetch (e.g. 'close-reading')")],
    ) -> str:
        content = self._loader.get_skill(skill_name)
        if content is None:
            available = [s["name"] for s in self._loader.list_skills()]
            return f"Skill '{skill_name}' not found. Available skills: {', '.join(available)}"
        return content
