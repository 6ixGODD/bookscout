"""Tests for the pretty log sink field rendering."""

from __future__ import annotations

import io
import re
import time

from bookscout.logging.record import LogRecord
from bookscout.logging.sink import PrettyStreamSink
from bookscout.logging.sink import _format_field_value
from bookscout.logging.sink import _format_fields

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _record(msg: str, fields: dict[str, object] | None = None) -> LogRecord:
    return LogRecord(
        ts=time.time(),
        level=20,
        name="test",
        message=msg,
        fields=fields or {},
    )


def test_format_fields_empty_returns_empty_string():
    assert _format_fields({}) == ""


def test_format_fields_renders_key_value_pairs_in_brackets():
    out = _format_fields({"path": "/mcp", "method": "POST"})
    assert out.startswith("  \x1b[35m[")
    assert out.endswith("]\x1b[0m")
    assert "path=/mcp" in out
    assert "method=POST" in out
    assert ", " in out  # comma-separated


def test_format_field_value_strings_bare_other_types_repr():
    assert _format_field_value("svc") == "svc"
    assert _format_field_value(8080) == "8080"
    assert _format_field_value(True) == "True"
    assert _format_field_value(None) == "None"
    assert _format_field_value([1, 2]) == "[1, 2]"


def test_pretty_sink_appends_fields_after_message():
    buf = io.StringIO()
    sink = PrettyStreamSink(buf, level=20)
    sink.write(_record("incoming request", {"path": "/mcp", "method": "POST"}))
    line = _strip_ansi(buf.getvalue().rstrip("\n"))

    assert "incoming request  [path=/mcp, method=POST]" in line
    # message comes before the bracketed fields
    assert line.index("incoming request") < line.index("[path=/mcp, method=POST]")


def test_pretty_sink_renders_without_fields():
    buf = io.StringIO()
    sink = PrettyStreamSink(buf, level=20)
    sink.write(_record("plain message"))
    line = _strip_ansi(buf.getvalue().rstrip("\n"))

    assert "plain message" in line
    # no bracketed fields suffix when there are no fields
    assert " [ " not in line
    assert not line.rstrip().endswith("]")
