"""Wire format reference for structured log records.

Every log line is a JSON object with these fields:

Required:
    ts      ISO 8601 UTC timestamp with millisecond precision
            e.g. "2026-01-01T12:00:00.123Z"
    level   "DEBUG" | "INFO" | "WARNING" | "ERROR" | "CRITICAL"
    name    Logger name / service identifier
    msg     Log message string

Optional (spread into top level):
    <any>   Bound context fields and call-site kwargs

Optional (structured sub-object):
    exc     Present only when exc_info=True
        type    Exception class name
        msg     str(exception)
        tb      Full traceback string
"""

from __future__ import annotations

import typing as t


class ExcInfo(t.TypedDict):
    type: str
    msg: str
    tb: str


class LogRecord(t.TypedDict, total=False):
    """TypedDict representation of the JSON wire format."""

    ts: t.Required[str]
    level: t.Required[str]
    name: t.Required[str]
    msg: t.Required[str]
    exc: ExcInfo
