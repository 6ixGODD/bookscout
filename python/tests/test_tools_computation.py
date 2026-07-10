"""Unit tests for the Python sandbox executor — numpy/scipy support.

Tests verify that numpy and scipy submodules are accessible in the sandbox,
that disallowed modules are blocked, and that existing stdlib modules still
work (non-regression).
"""

from __future__ import annotations

import pytest

from bookscout.tools.computation import PythonExecuteTool


class TestPythonSandbox:
    """Sandbox: allowed modules, submodule getattr chain, blocked modules."""

    @pytest.fixture()
    def tool(self) -> PythonExecuteTool:
        return PythonExecuteTool()

    async def test_numpy_is_available(self, tool: PythonExecuteTool) -> None:
        """numpy is accessible in sandbox globals and produces correct results."""
        result = await tool("print(numpy.array([1, 2, 3]).sum())")
        assert result.strip() == "6"

    async def test_scipy_submodule_via_getattr_chain(self, tool: PythonExecuteTool) -> None:
        """scipy.stats is reachable through the getattr chain."""
        result = await tool("print(float(scipy.stats.norm.pdf(0)))")
        # norm.pdf(0) == 0.3989422804014327
        assert "0.398" in result

    async def test_blocked_module_returns_error(self, tool: PythonExecuteTool) -> None:
        """Importing a disallowed module raises an error, not a silent pass."""
        result = await tool("import os")
        assert "Error" in result or "error" in result

    async def test_stdlib_math_still_works(self, tool: PythonExecuteTool) -> None:
        """Existing stdlib modules (math) remain usable in the sandbox."""
        result = await tool("print(math.sqrt(16))")
        assert result.strip() == "4.0"
