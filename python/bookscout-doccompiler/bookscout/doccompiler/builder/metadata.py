"""LLM-based metadata extraction (spec §7.5, §16.6).

Uses :mod:`bookscout.llm` to identify book metadata (title, author, ISBN,
publisher, language, extras) from the first few content fragments of
``CONTENT.md``. Supports supplementing and correcting across multiple
rounds. Unrecognized fields default to empty strings.
"""

from __future__ import annotations

import dataclasses
import json
import typing as t

from bookscout.core.mixins import AsyncResourceMixin
from bookscout.logging.mixin import LoggingMixin

if t.TYPE_CHECKING:
    from bookscout.llm import ChatModel
    from bookscout.logging import Logger

_SYSTEM_PROMPT = """\
You are a book metadata extractor. Given a fragment of a book's content,
extract the following fields if present:
- title: the book title
- author: the author name(s)
- isbn: the ISBN identifier
- publisher: the publisher name
- language: the language code or name

Return ONLY a JSON object with these fields. Use empty string "" for
fields you cannot determine from the fragment. Do not add prose,
code fences, or explanations.

Example: {"title": "Refactoring", "author": "Martin Fowler", "isbn": "9780134757599", "publisher": "Addison-Wesley", "language": "en"}
"""

_MAX_FRAGMENT_CHARS = 2000
_MAX_ROUNDS = 3


@dataclasses.dataclass(slots=True)
class ExtractedMetadata:
    """Result of LLM metadata extraction.

    Attributes:
        title: Book title (empty string if unrecognized).
        author: Author (empty string if unrecognized).
        isbn: ISBN (empty string if unrecognized).
        publisher: Publisher (empty string if unrecognized).
        language: Language (empty string if unrecognized).
        extras: Extra metadata fields.
        stop_reason: Why extraction stopped (e.g. "max_rounds", "no_more_metadata").
    """

    title: str
    author: str
    isbn: str
    publisher: str
    language: str
    extras: dict[str, t.Any]
    stop_reason: str


class LlmMetadataExtractor(LoggingMixin, AsyncResourceMixin):
    """Extracts book metadata from content fragments using an LLM.

    Args:
        logger: Logger instance.
        model: A :class:`bookscout.llm.ChatModel` instance (must be started).
        max_fragment_chars: Max chars per content fragment.
        max_rounds: Max extraction rounds.
    """

    def __init__(
        self,
        logger: Logger,
        model: ChatModel,
        max_fragment_chars: int = _MAX_FRAGMENT_CHARS,
        max_rounds: int = _MAX_ROUNDS,
    ) -> None:
        super().__init__(logger=logger)
        self._model = model
        self._max_fragment_chars = max_fragment_chars
        self._max_rounds = max_rounds

    async def extract(self, content: str) -> ExtractedMetadata:
        """Extract metadata from ``CONTENT.md`` text.

        Sends the first few content fragments to the LLM, progressively
        identifying and correcting metadata fields. Stops when:
            - All fields are identified.
            - Max rounds reached.
            - LLM reports no more metadata likely.

        Args:
            content: The full ``CONTENT.md`` text.

        Returns:
            An :class:`ExtractedMetadata` with all fields (empty strings
            for unrecognized ones).
        """
        from bookscout.llm.types import CompletionOptions
        from bookscout.llm.types import SystemMessage
        from bookscout.llm.types import UserMessage

        fragments = self._split_fragments(content)
        self.logger.info("metadata extraction starting", fragments=len(fragments))

        result = ExtractedMetadata(
            title="",
            author="",
            isbn="",
            publisher="",
            language="",
            extras={},
            stop_reason="max_rounds",
        )

        for round_idx, fragment in enumerate(fragments[: self._max_rounds]):
            self.logger.debug(
                "extraction round",
                round=round_idx,
                fragment_chars=len(fragment),
            )

            prompt = self._build_prompt(fragment, result)
            response = await self._model.chat_completion(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    UserMessage(content=prompt),
                ],
                options=CompletionOptions(max_tokens=1024, temperature=0.0),
            )
            raw_text = response["message"].content
            finish_reason = response.get("finish_reason", "")
            self.logger.info(
                "llm response",
                round=round_idx,
                finish_reason=finish_reason,
                output_tokens=response.get("usage", {}).get("output_tokens", 0),
                response_preview=raw_text[:400],
            )

            parsed = self._parse_response(raw_text)
            if parsed is None:
                self.logger.warning("failed to parse LLM response as JSON", round=round_idx)
                continue

            result = self._merge_metadata(result, parsed)
            self.logger.info(
                "extraction round result",
                round=round_idx,
                title=result.title,
                author=result.author,
            )

            if self._is_complete(result):
                result.stop_reason = "all_fields_identified"
                break
        else:
            result.stop_reason = "max_rounds"

        self.logger.info(
            "metadata extraction finished",
            stop_reason=result.stop_reason,
            title=result.title,
            author=result.author,
        )
        return result

    def _split_fragments(self, content: str) -> list[str]:
        """Split content into fragments for LLM processing.

        Takes the first ``max_rounds * max_fragment_chars`` characters,
        split into roughly equal fragments.

        Args:
            content: Full content text.

        Returns:
            List of content fragments.
        """
        total = self._max_rounds * self._max_fragment_chars
        truncated = content[:total]
        if not truncated:
            return [""]
        # Split on paragraph boundaries when possible.
        fragments: list[str] = []
        for i in range(0, len(truncated), self._max_fragment_chars):
            chunk = truncated[i : i + self._max_fragment_chars]
            fragments.append(chunk)
        return fragments

    @staticmethod
    def _build_prompt(fragment: str, current: ExtractedMetadata) -> str:
        """Build the user prompt for one extraction round.

        Args:
            fragment: Content fragment text.
            current: Current metadata state (for correction context).

        Returns:
            The prompt string.
        """
        lines = [
            f"Here is fragment of a book's content ({len(fragment)} chars):",
            "",
            fragment,
            "",
        ]
        has_any = any([current.title, current.author, current.isbn, current.publisher, current.language])
        if has_any:
            lines.append(
                f"Current metadata so far: title={current.title!r}, author={current.author!r}, "
                f"isbn={current.isbn!r}, publisher={current.publisher!r}, language={current.language!r}."
            )
            lines.append("Please correct or supplement these fields if the fragment contains better information.")
        else:
            lines.append("Please extract any metadata you can identify from this fragment.")
        lines.append("Return ONLY a JSON object.")
        return "\n".join(lines)

    @staticmethod
    def _parse_response(raw: str) -> dict[str, t.Any] | None:
        """Parse the LLM response as JSON.

        Handles common issues: code fences, extra prose.

        Args:
            raw: Raw LLM response text.

        Returns:
            Parsed dict or ``None`` if unparseable.
        """
        text = raw.strip()
        # Strip code fences if present.
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last fence lines.
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        # Find the first { and last } to extract JSON.
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < 0 or end <= start:
            return None
        try:
            return t.cast(dict[str, t.Any], json.loads(text[start : end + 1]))
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _merge_metadata(current: ExtractedMetadata, parsed: dict[str, t.Any]) -> ExtractedMetadata:
        """Merge parsed LLM output into current metadata.

        Only overwrites non-empty values from the parsed dict. Empty strings
        in the parsed dict do not overwrite existing non-empty values
        (§7.5: corrections are explicit).

        Args:
            current: Current metadata state.
            parsed: Parsed LLM JSON output.

        Returns:
            Updated ExtractedMetadata.
        """

        def _merge(field: str) -> str:
            val = str(parsed.get(field, "") or "").strip()
            if val:
                return val
            return str(getattr(current, field))

        # Handle extras: merge any non-standard fields.
        extras = dict(current.extras)
        for k, v in parsed.items():
            if k not in ("title", "author", "isbn", "publisher", "language") and v and str(v).strip():
                extras[k] = str(v).strip()

        return ExtractedMetadata(
            title=_merge("title"),
            author=_merge("author"),
            isbn=_merge("isbn"),
            publisher=_merge("publisher"),
            language=_merge("language"),
            extras=extras,
            stop_reason=current.stop_reason,
        )

    @staticmethod
    def _is_complete(meta: ExtractedMetadata) -> bool:
        """Check if all core metadata fields are non-empty.

        Args:
            meta: Current metadata.

        Returns:
            ``True`` if title, author, isbn, publisher, and language are all set.
        """
        return all([meta.title, meta.author, meta.isbn, meta.publisher, meta.language])
