"""IndexRegistry — runtime catalogue of available IndexProviders.

Providers are discovered via Python entry_points (group ``"bookscout.indexes"``).
Each entry point value is an :class:`~bookscout.doccompiler.index_provider.IndexProvider`
instance. The registry is a thin wrapper exposing typed lookups; callers
never import a specific index package.
"""

from __future__ import annotations

import importlib.metadata

from bookscout.doccompiler.index_provider import IndexProvider


class IndexRegistry:
    """Runtime catalogue of available :class:`IndexProvider` descriptors."""

    def __init__(self, providers: list[IndexProvider]) -> None:
        self._providers = list(providers)

    @classmethod
    def load(cls) -> IndexRegistry:
        """Discover providers via the ``bookscout.indexes`` entry-point group."""
        eps = importlib.metadata.entry_points(group="bookscout.indexes")
        providers: list[IndexProvider] = []
        for ep in eps:
            obj = ep.load()
            if isinstance(obj, IndexProvider):
                providers.append(obj)
        return cls(providers)

    def all(self) -> list[IndexProvider]:
        """Return all registered providers in registration order."""
        return list(self._providers)

    def for_types(self, types: set[str]) -> list[IndexProvider]:
        """Return providers whose index_type is in ``types``."""
        return [p for p in self._providers if p.index_type in types]

    def default_enabled(self) -> list[IndexProvider]:
        """Return providers flagged ``default_enabled=True``."""
        return [p for p in self._providers if p.default_enabled]

    def by_type(self, index_type: str) -> IndexProvider | None:
        """Return the provider for ``index_type`` or ``None``."""
        for p in self._providers:
            if p.index_type == index_type:
                return p
        return None

    @property
    def letters(self) -> str:
        """Concatenation of all providers' short_letters in registration order."""
        return "".join(p.short_letter for p in self._providers)


__all__ = ["IndexRegistry"]
