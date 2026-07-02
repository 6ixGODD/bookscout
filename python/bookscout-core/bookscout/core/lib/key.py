from __future__ import annotations


class KeyBuilder:
    """Utility class for building keys with a consistent format.

    Provides methods to construct namespaced keys and validate key formats.

    Attributes:
        split_char: Character used to split parts of the key.
        prefix: Prefix to prepend to all keys.

    Args:
        split_char: Separator character for key parts. Defaults to ":".
        prefix: Namespace prefix for all keys. Defaults to normalized project title.

    Example:
        ```python
        builder = KeyBuilder(prefix="myapp")

        # Build keys
        user_key = builder.build("user", "123")  # "myapp:user:123"
        session_key = builder.build("session", "abc")  # "myapp:session:abc"

        # Validate keys
        builder.validate("myapp:user:123")  # True
        builder.validate("other:user:123")  # False
        ```
    """

    __slots__ = ("prefix", "split_char")

    def __init__(self, split_char: str = ":", prefix: str | None = None) -> None:
        self.split_char = split_char
        self.prefix = prefix or ""

    def build(self, *parts_args: str, **parts_kwargs: str) -> str:
        """Build a key by joining the prefix and parts.

        Args:
            *parts_args: Parts to include in the key.
            **parts_kwargs: Key-value pairs to include in the key.

        Returns:
            The constructed key.
        """
        parts = list(parts_args)
        for k, v in parts_kwargs.items():
            parts.append(f"{k}={v}")
        return self.split_char.join((self.prefix, *parts) if self.prefix else parts)

    def parse(self, key: str) -> tuple[list[str], dict[str, str]]:
        """Parse a key into its constituent parts, excluding the prefix.

        Args:
            key: The key to parse.

        Returns:
            A tuple containing a list of positional parts and a dictionary of
            key-value pairs excluding the prefix.
        """
        if not self.validate(key):
            raise ValueError(f"Key '{key}' does not start with the prefix '{self.prefix}'")
        parts = key[len(self.prefix) + len(self.split_char) :].split(self.split_char)
        positional_parts = []
        kv_dict = {}
        for part in parts:
            if "=" in part:
                k, v = part.split("=", 1)
                kv_dict[k] = v
            else:
                positional_parts.append(part)
        return positional_parts, kv_dict

    def validate(self, key: str) -> bool:
        """Validate if a given key starts with the defined prefix.

        Args:
            key: The key to validate.

        Returns:
            True if the key starts with the prefix, False otherwise.
        """
        if not key.startswith(self.prefix + self.split_char):
            return False

        parts = key[len(self.prefix) + len(self.split_char) :].split(self.split_char)

        seen_kw = False
        for p in parts:
            if "=" in p:
                seen_kw = True
            else:
                if seen_kw:
                    return False
        return True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(prefix={self.prefix}, split_char={self.split_char})"

    __str__ = __repr__
