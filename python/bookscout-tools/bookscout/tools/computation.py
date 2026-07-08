"""Computation tools — Wolfram formula evaluation and Python sandbox execution.

These tools allow the LLM to perform calculations and code execution
for scientific computing scenarios. Both are sandboxed — no filesystem
access, no network, no imports beyond the standard library math/science
modules.
"""

from __future__ import annotations

import typing as t
from typing import Annotated

from bookscout.tools import BaseTool
from bookscout.tools import Property

# Python sandbox: allowed modules for scientific computing.
_ALLOWED_MODULES = frozenset({
    "math",
    "statistics",
    "itertools",
    "functools",
    "operator",
    "decimal",
    "fractions",
    "random",
    "array",
    "json",
    "re",
    "datetime",
    "collections",
    "dataclasses",
    "typing",
    "hashlib",
    "base64",
    "textwrap",
    "string",
    "unicodedata",
    "cmath",
    "numbers",
})


class WolframExecuteTool(
    BaseTool,
    name="wolfram_execute",
    description="Evaluate a Wolfram-style mathematical expression. Input is a Wolfram expression string (e.g. 'Integrate[x^2, x]' or 'Solve[x^2 - 4 == 0, x]'). Returns the result as text, or an error message. Use for symbolic math, calculus, algebra, equation solving.",
):
    """Tool: wolfram_execute — evaluate Wolfram-style math expressions.

    This is a local evaluator that handles basic Wolfram syntax.
    For complex expressions, it falls back to Python's sympy if available,
    otherwise returns an error suggesting the user try python_execute.
    """

    async def __call__(
        self,
        expression: Annotated[
            str, Property(description="Wolfram-style expression, e.g. 'D[x^2, x]' or 'Solve[x^2==4, x]'")
        ],
    ) -> str:
        try:
            result = self._evaluate(expression)
            return f"Result: {result}"
        except Exception as e:
            return f"Error evaluating expression: {e}\nFor complex computations, try the python_execute tool instead."

    def _evaluate(self, expr: str) -> str:
        """Evaluate a Wolfram-style expression locally.

        Handles common patterns by translating to Python/sympy.
        """
        try:
            import sympy

            _x, _y, _z = sympy.symbols("x y z")

            # Translate common Wolfram functions.
            expr_clean = expr.strip()

            # Basic patterns.
            if expr_clean.startswith("D[") and expr_clean.endswith("]"):
                # D[f, x] → diff(f, x)
                inner = expr_clean[2:-1]
                parts = inner.split(",")
                if len(parts) == 2:
                    f_str = parts[0].strip().replace("^", "**")
                    var_str = parts[1].strip()
                    f = sympy.sympify(f_str)
                    result = sympy.diff(f, sympy.Symbol(var_str))
                    return str(result)

            if expr_clean.startswith("Integrate[") and expr_clean.endswith("]"):
                inner = expr_clean[10:-1]
                parts = inner.split(",")
                if len(parts) == 2:
                    f_str = parts[0].strip().replace("^", "**")
                    var_str = parts[1].strip()
                    f = sympy.sympify(f_str)
                    result = sympy.integrate(f, sympy.Symbol(var_str))
                    return str(result)

            if expr_clean.startswith("Solve[") and expr_clean.endswith("]"):
                inner = expr_clean[6:-1]
                parts = inner.split(",")
                if len(parts) == 2:
                    eq_str = parts[0].strip().replace("^", "**").replace("==", "-(") + ")"
                    var_str = parts[1].strip()
                    eq = sympy.sympify(eq_str)
                    result = sympy.solve(eq, sympy.Symbol(var_str))
                    return str(result)

            if expr_clean.startswith("Simplify[") and expr_clean.endswith("]"):
                inner = expr_clean[9:-1]
                f = sympy.sympify(inner.replace("^", "**"))
                return str(sympy.simplify(f))

            # Direct sympify for simple expressions.
            translated = expr_clean.replace("^", "**").replace("{", "(").replace("}", ")")
            result = sympy.sympify(translated)
            return str(result)

        except ImportError:
            # No sympy — try basic eval.
            try:
                translated = expr.replace("^", "**").replace("{", "(").replace("}", ")")
                # Only allow safe operations.
                import ast

                tree = ast.parse(translated, mode="eval")
                result = eval(compile(tree, "<wolfram>", "eval"), {"__builtins__": {}}, {})
                return str(result)
            except Exception as e:
                raise RuntimeError(f"Cannot evaluate '{expr}': {e}. Install sympy for full Wolfram support.") from e


class PythonExecuteTool(
    BaseTool,
    name="python_execute",
    description="Execute Python code in a sandboxed environment. Input is a Python code string. Returns stdout output. No filesystem, no network, no external packages beyond stdlib math/science modules. Use for calculations, data processing, scientific computing. Print results with print().",
):
    """Tool: python_execute — sandboxed Python code execution.

    The sandbox:
        - No filesystem access (no open, os, subprocess, etc.)
        - No network access
        - Only stdlib math/science modules allowed
        - Runs in a restricted exec scope
        - Captures stdout and stderr
        - 10-second timeout
    """

    async def __call__(
        self,
        code: Annotated[
            str,
            Property(
                description="Python code to execute. Print results with print(). Only stdlib math/science modules allowed."
            ),
        ],
    ) -> str:
        import contextlib
        import io
        import threading

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # Build restricted globals.
        safe_globals: dict[str, t.Any] = {"__builtins__": {}}
        # Add allowed builtins.
        safe_builtins = {
            "print": print,
            "range": range,
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "enumerate": enumerate,
            "zip": zip,
            "map": map,
            "filter": filter,
            "sorted": sorted,
            "reversed": reversed,
            "sum": sum,
            "min": min,
            "max": max,
            "abs": abs,
            "round": round,
            "all": all,
            "any": any,
            "type": type,
            "isinstance": isinstance,
            "repr": repr,
            "format": format,
            "frozenset": frozenset,
            "True": True,
            "False": False,
            "None": None,
        }
        safe_globals["__builtins__"] = safe_builtins

        # Add allowed modules.
        for mod_name in _ALLOWED_MODULES:
            try:
                __import__(mod_name)
                safe_globals[mod_name] = __import__(mod_name)
            except ImportError:
                pass

        # Execute with timeout.
        result_holder: dict[str, t.Any] = {"error": None}

        def _run() -> None:
            try:
                with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                    exec(code, safe_globals)
            except Exception as e:
                result_holder["error"] = str(e)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=10.0)

        if thread.is_alive():
            return "Error: execution timed out (10s limit exceeded)."

        stdout_text = stdout_buf.getvalue()
        stderr_text = stderr_buf.getvalue()

        if result_holder["error"]:
            return (
                f"Error: {result_holder['error']}\n\nStdout:\n{stdout_text}"
                if stdout_text
                else f"Error: {result_holder['error']}"
            )

        if stderr_text:
            return f"Stdout:\n{stdout_text}\n\nStderr:\n{stderr_text}" if stdout_text else f"Stderr:\n{stderr_text}"

        return stdout_text if stdout_text else "(no output)"


def create_computation_tools() -> list[BaseTool]:
    """Create computation tools (wolfram + python sandbox).

    Returns:
        List of BaseTool instances.
    """
    return [WolframExecuteTool(), PythonExecuteTool()]


__all__ = [
    "PythonExecuteTool",
    "WolframExecuteTool",
    "create_computation_tools",
]
