from __future__ import annotations

import pathlib
import re
import typing as t

KEBAB_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z][a-z0-9]*)*$")


def name_to_ns(name: str) -> str:
    """``'bookscout-foo-bar'`` → ``'bookscout.foo.bar'``"""
    return name.replace("-", ".")


def name_to_rel_path(name: str) -> pathlib.Path:
    """``'bookscout-foo-bar'`` → ``Path('bookscout/foo/bar')``"""
    return pathlib.Path(*name.split("-"))


def check_namespace_conflict(new_name: str, existing: list[str]) -> tuple[bool, str]:
    """Return ``(True, reason)`` when *new_name* conflicts with any name in *existing*.

    A conflict occurs when one package name's segment-parts are a strict prefix
    of the other's, meaning one would be a non-leaf ancestor in the namespace tree.

    Examples::

        bookscout-infra    vs  bookscout-infra-db      → conflict (ancestor)
        bookscout-infra-db vs  bookscout-infra-db-ext  → conflict (leaf has child)
        bookscout-core     vs  bookscout-infra-db      → ok
    """
    new_parts = new_name.split("-")
    for name in existing:
        parts = name.split("-")
        if new_parts == parts:
            return True, f"Package '{new_name}' already exists."
        min_len = min(len(new_parts), len(parts))
        if new_parts[:min_len] == parts[:min_len]:
            if len(new_parts) < len(parts):
                return (
                    True,
                    f"'{new_name}' would be an ancestor namespace of the existing '{name}',\n"
                    f"preventing it from being used as a namespace package.",
                )
            return (
                True,
                f"Existing '{name}' is an ancestor namespace of '{new_name}'.\n"
                f"A sub-namespace of a leaf package cannot be created.",
            )
    return False, ""


def build_ns_tree(packages: list[str]) -> dict[str, t.Any]:
    """Build a nested dict representing the namespace segment tree."""
    tree: dict[str, t.Any] = {}
    for pkg_name in sorted(packages):
        node = tree
        for part in pkg_name.split("-"):
            node = node.setdefault(part, {})
    return tree


def print_ns_tree(tree: dict[str, t.Any], prefix: str = "") -> None:
    """Recursively pretty-print the namespace tree to stdout."""
    from scripts._helpers import console

    items = list(tree.items())
    for i, (name, subtree) in enumerate(items):
        is_last = i == len(items) - 1
        connector = "└── " if is_last else "├── "
        extension = "    " if is_last else "│   "
        if subtree:
            print(f"{prefix}{connector}{console.format_bold(name)}")
            print_ns_tree(subtree, prefix + extension)
        else:
            print(f"{prefix}{connector}{console.format_cyan(name)} {console.format_dim('(leaf)')}")
