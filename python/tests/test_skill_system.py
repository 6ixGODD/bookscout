from __future__ import annotations

import pathlib
import tempfile

from bookscout.repl.config import SkillConfig
from bookscout.repl.skill_loader import SkillLoader
from bookscout.repl.prompt_builder import PromptBuilder


def test_skill_loader_list():
    skills = [
        SkillConfig(name="test-skill", path="test-skill.md", description="A test skill"),
    ]
    loader = SkillLoader(workdir=pathlib.Path("/tmp"), skills=skills)
    result = loader.list_skills()
    assert len(result) == 1
    assert result[0]["name"] == "test-skill"


def test_skill_loader_get_missing():
    loader = SkillLoader(workdir=pathlib.Path("/tmp"), skills=[])
    assert loader.get_skill("nonexistent") is None


def test_skill_loader_get_cached():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        skills_dir = workdir / "skills"
        skills_dir.mkdir()
        skill_file = skills_dir / "test-skill.md"
        skill_file.write_text("# Test Skill\nContent here.")

        skills = [SkillConfig(name="test-skill", path="test-skill.md", description="desc")]
        loader = SkillLoader(workdir=workdir, skills=skills)
        content = loader.get_skill("test-skill")
        assert content == "# Test Skill\nContent here."
        # Second call hits cache
        content2 = loader.get_skill("test-skill")
        assert content2 == content


def test_prompt_builder_basic():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        builder = PromptBuilder(
            skill_descriptions=[{"name": "s1", "description": "d1"}],
            soul_path=workdir / "SOUL.md",
            base_system_prompt="BASE PROMPT",
        )
        result = builder.build()
        assert "## Available Skills" in result
        assert "- **s1**: d1" in result
        assert "BASE PROMPT" in result
        assert "Current date:" in result
        assert "Current time:" in result


def test_prompt_builder_with_soul():
    with tempfile.TemporaryDirectory() as tmp:
        workdir = pathlib.Path(tmp)
        soul_path = workdir / "SOUL.md"
        soul_path.write_text("You are a wise librarian.")

        builder = PromptBuilder(
            skill_descriptions=[],
            soul_path=soul_path,
            base_system_prompt="BASE",
        )
        result = builder.build()
        assert "You are a wise librarian." in result


def test_prompt_builder_no_skills():
    builder = PromptBuilder(
        skill_descriptions=[],
        soul_path=pathlib.Path("/nonexistent"),
        base_system_prompt="BASE",
    )
    result = builder.build()
    assert "## Available Skills" not in result
    assert "BASE" in result
