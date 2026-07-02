from __future__ import annotations

import base64
import mimetypes
import pathlib
import typing as t


class Base64(str):
    """A pathlib-like class for Base64 encoding/decoding manipulation.

    This class provides a fluent interface for encoding, decoding, and
    manipulating Base64 data. It supports standard and URL-safe Base64
    encoding, MIME type tracking, and convenient methods for working with
    images, audio, and other binary data.

    **Design Philosophy:**
        - Immutable: all operations return new instances
        - Type-safe: preserves MIME type information
        - Flexible: supports standard and URL-safe encoding
        - Convenient: fluent API for common operations

    Attributes:
        data: The Base64-encoded string (without data URI prefix).
        mime_type: Optional MIME type of the encoded data (e.g., 'image/png',
            'audio/mp3').
        encoding: The encoding variant ('standard' or 'urlsafe'). Defaults to
            'standard'.

    Examples:
        ```python
        # Encode bytes
        b64 = Base64.from_bytes(b"Hello, World!")
        str(b64)
        # 'SGVsbG8sIFdvcmxkIQ=='

        # Decode to bytes
        b64.to_bytes()
        # b'Hello, World!'

        # Encode image file
        img = Base64.from_file("photo.png")
        img.mime_type
        # 'image/png'

        # Data URI format
        img.to_data_uri()
        # 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA...'

        # URL-safe encoding
        b64 = Base64.from_bytes(b"data", urlsafe=True)
        str(b64)
        # 'ZGF0YQ' (no padding, URL-safe)
        ```
    """

    __slots__ = ("data", "encoding", "mime_type")

    data: str
    encoding: t.Literal["standard", "urlsafe"]
    mime_type: str | None

    def __new__(
        cls,
        data: str | Base64 | None = None,
        *,
        mime_type: str | None = None,  # noqa: ARG004
        encoding: t.Literal["standard", "urlsafe"] = "standard",  # noqa: ARG004
    ) -> t.Self:
        if isinstance(data, Base64):
            s = data.data
        elif isinstance(data, str):
            # Strip data URI prefix if present
            s = (data.split(",", 1)[1] if "," in data else data) if data.startswith("data:") else data
        else:
            s = ""

        return super().__new__(cls, s)

    def __init__(
        self,
        data: str | Base64 | None = None,
        *,
        mime_type: str | None = None,
        encoding: t.Literal["standard", "urlsafe"] = "standard",
    ):
        """Initializes a Base64 instance.

        Args:
            data: A Base64-encoded string, Base64 instance, or data URI to parse.
                If data URI format is provided (e.g., 'data:image/png;base64,...'),
                the MIME type will be automatically extracted.
            mime_type: Optional MIME type of the data. If not provided and data
                is a data URI, it will be extracted automatically.
            encoding: The encoding variant to use. Either 'standard' (uses +/=)
                or 'urlsafe' (uses -_). Defaults to 'standard'.

        Examples:
            ```python
            # From base64 string
            Base64("SGVsbG8=")

            # From data URI
            Base64("data:image/png;base64,iVBORw0KG...")

            # With explicit MIME type
            Base64("SGVsbG8=", mime_type="text/plain")
            ```
        """
        if isinstance(data, Base64):
            self.data = data.data
            self.mime_type = mime_type or data.mime_type
            self.encoding = encoding if encoding != "standard" else data.encoding
        elif isinstance(data, str):
            # Parse data URI if present
            if data.startswith("data:"):
                if "," in data:
                    header, encoded_data = data.split(",", 1)
                    self.data = encoded_data
                    # Extract MIME type from header: data:image/png;base64
                    if ";" in header:
                        extracted_mime = header.split(":", 1)[1].split(";", 1)[0]
                        self.mime_type = mime_type or extracted_mime
                    else:
                        self.mime_type = mime_type
                else:
                    self.data = data
                    self.mime_type = mime_type
            else:
                self.data = data
                self.mime_type = mime_type
            self.encoding = encoding
        else:
            self.data = ""
            self.mime_type = mime_type
            self.encoding = encoding

    def __str__(self) -> str:
        """Returns the Base64-encoded string (without data URI prefix).

        Returns:
            The Base64-encoded string.

        Examples:
            ```python
            b64 = Base64.from_bytes(b"Hello")
            str(b64)
            # 'SGVsbG8='
            ```
        """
        return self.data

    def __repr__(self) -> str:
        parts = [f"'{self.data[:20]}{'...' if len(self.data) > 20 else ''}'"]
        if self.mime_type:
            parts.append(f"mime_type='{self.mime_type}'")
        if self.encoding != "standard":
            parts.append(f"encoding='{self.encoding}'")
        return f"{self.__class__.__name__}({', '.join(parts)})"

    def __eq__(self, other: object) -> bool:
        """Compares two Base64 instances for equality.

        Args:
            other: Another Base64 instance or string to compare with.

        Returns:
            True if the encoded data is equal, False otherwise.
        """
        if isinstance(other, str):
            other = Base64(other)
        elif not isinstance(other, Base64):
            return NotImplemented

        return self.data == other.data and self.mime_type == other.mime_type

    def __hash__(self) -> int:
        """Returns the hash of the Base64 instance.

        Returns:
            Hash value of the encoded data.
        """
        return hash((self.data, self.mime_type))

    def __reduce__(self) -> tuple[type[Base64], tuple[str, str | None, str]]:
        """Support for pickling the Base64 instance.

        Returns:
            A tuple containing the class and its initialization arguments.
        """
        return self.__class__, (self.data, self.mime_type, self.encoding)

    def __getnewargs_ex__(self) -> tuple[tuple[str], dict[str, t.Any]]:
        """Support for pickling with __getnewargs_ex__.

        Returns:
            A tuple containing positional and keyword arguments for reconstruction.
        """
        return (self.data,), {"mime_type": self.mime_type, "encoding": self.encoding}

    def __getstate__(self) -> None:
        """Support for pickling the Base64 instance."""
        return

    def __setstate__(self, state: None) -> None:
        """Support for unpickling the Base64 instance."""

    def __copy__(self) -> Base64:
        """Creates a shallow copy of the Base64 instance."""
        return Base64(
            self.data,
            mime_type=self.mime_type,
            encoding=self.encoding,
        )

    def __deepcopy__(self, memo: dict[int, t.Any]) -> Base64:
        """Creates a deep copy of the Base64 instance."""
        return self.__copy__()

    def __bool__(self) -> bool:
        """Determines the truthiness of the Base64 instance.

        Returns:
            True if data is non-empty, False otherwise.
        """
        return bool(self.data)

    def __len__(self) -> int:
        """Returns the length of the Base64-encoded string.

        Returns:
            The length of the encoded data.
        """
        return len(self.data)

    # Encoding methods

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        mime_type: str | None = None,
        urlsafe: bool = False,
    ) -> Base64:
        """Encodes bytes to Base64.

        Args:
            data: Raw bytes to encode.
            mime_type: Optional MIME type of the data.
            urlsafe: If True, uses URL-safe encoding (- and _ instead of + and /).
                Defaults to False.

        Returns:
            A new Base64 instance.

        Examples:
            ```python
            # Standard encoding
            Base64.from_bytes(b"Hello, World!")
            # Base64('SGVsbG8sIFdvcmxkIQ==')

            # URL-safe encoding
            Base64.from_bytes(b"data>>>", urlsafe=True)
            # Base64('ZGF0YT4-Pg', encoding='urlsafe')
            ```
        """
        if urlsafe:
            encoded = base64.urlsafe_b64encode(data).decode("ascii")
            encoding: t.Literal["standard", "urlsafe"] = "urlsafe"
        else:
            encoded = base64.b64encode(data).decode("ascii")
            encoding = "standard"

        return cls(encoded, mime_type=mime_type, encoding=encoding)

    @classmethod
    def from_file(
        cls,
        path: str | pathlib.Path,
        *,
        mime_type: str | None = None,
        urlsafe: bool = False,
    ) -> Base64:
        """Encodes a file's contents to Base64.

        MIME type is automatically detected from file extension if not provided.

        Args:
            path: pathlib.Path to the file to encode.
            mime_type: Optional MIME type. If not provided, will be guessed from
                file extension.
            urlsafe: If True, uses URL-safe encoding. Defaults to False.

        Returns:
            A new Base64 instance.

        Examples:
            ```python
            # Encode image
            img = Base64.from_file("photo.png")
            img.mime_type
            # 'image/png'

            # Encode audio
            audio = Base64.from_file("song.mp3")
            audio.mime_type
            # 'audio/mpeg'
            ```
        """
        path = pathlib.Path(path)

        # Read file bytes
        data = path.read_bytes()

        # Guess MIME type if not provided
        if mime_type is None:
            mime_type, _ = mimetypes.guess_type(str(path))

        return cls.from_bytes(data, mime_type=mime_type, urlsafe=urlsafe)

    # Decoding methods

    def to_bytes(self) -> bytes:
        """Decodes Base64 to bytes.

        Returns:
            The decoded bytes.

        Examples:
            ```python
            b64 = Base64("SGVsbG8=")
            b64.to_bytes()
            # b'Hello'
            ```
        """
        if self.encoding == "urlsafe":
            # Add padding if needed
            missing_padding = len(self.data) % 4
            padded = self.data + "=" * (4 - missing_padding) if missing_padding else self.data
            return base64.urlsafe_b64decode(padded)
        return base64.b64decode(self.data)

    def to_file(self, path: str | pathlib.Path) -> None:
        """Decodes Base64 and writes to a file.

        Args:
            path: pathlib.Path where the decoded data should be written.

        Examples:
            ```python
            b64 = Base64.from_file("original.png")
            b64.to_file("copy.png")
            ```
        """
        path = pathlib.Path(path)
        path.write_bytes(self.to_bytes())

    # Format conversion methods

    def to_data_uri(self) -> str:
        """Converts to data URI format.

        Returns:
            Data URI string (e.g., 'data:image/png;base64,...').

        Raises:
            ValueError: If mime_type is not set.

        Examples:
            ```python
            b64 = Base64.from_bytes(b"Hello", mime_type="text/plain")
            b64.to_data_uri()
            # 'data:text/plain;base64,SGVsbG8='
            ```
        """
        if not self.mime_type:
            raise ValueError("Cannot create data URI without mime_type")

        return f"data:{self.mime_type};base64,{self.data}"

    @classmethod
    def from_data_uri(cls, uri: str) -> Base64:
        """Parses a data URI into a Base64 instance.

        Args:
            uri: Data URI string (e.g., 'data:image/png;base64,...').

        Returns:
            A new Base64 instance with extracted MIME type.

        Raises:
            ValueError: If the URI format is invalid.

        Examples:
            ```python
            uri = "data:image/png;base64,iVBORw0KG..."
            b64 = Base64.from_data_uri(uri)
            b64.mime_type
            # 'image/png'
            ```
        """
        if not uri.startswith("data:"):
            raise ValueError("Invalid data URI: must start with 'data:'")

        return cls(uri)

    # Encoding conversion methods
    def as_urlsafe(self) -> Base64:
        """Converts to URL-safe Base64 encoding.

        URL-safe encoding uses - and _ instead of + and /, and typically
        omits padding (=).

        Returns:
            A new Base64 instance with URL-safe encoding.

        Examples:
            ```python
            b64 = Base64.from_bytes(b"data>>>")
            standard = str(b64)
            # 'ZGF0YT4+Pg=='

            urlsafe = str(b64.as_urlsafe())
            # 'ZGF0YT4-Pg'
            ```
        """
        if self.encoding == "urlsafe":
            return self

        # Decode and re-encode as URL-safe
        data = self.to_bytes()
        return self.from_bytes(data, mime_type=self.mime_type, urlsafe=True)

    def as_standard(self) -> Base64:
        """Converts to standard Base64 encoding.

        Standard encoding uses + and / and includes padding (=).

        Returns:
            A new Base64 instance with standard encoding.

        Examples:
            ```python
            b64 = Base64.from_bytes(b"data", urlsafe=True)
            b64.as_standard()
            # Base64('ZGF0YQ==', encoding='standard')
            ```
        """
        if self.encoding == "standard":
            return self

        # Decode and re-encode as standard
        data = self.to_bytes()
        return self.from_bytes(data, mime_type=self.mime_type, urlsafe=False)

    # MIME type methods

    def with_mime_type(self, mime_type: str) -> Base64:
        """Returns a new instance with a different MIME type.

        Args:
            mime_type: The new MIME type (e.g., 'image/jpeg', 'audio/mp3').

        Returns:
            A new Base64 instance with updated MIME type.

        Examples:
            ```python
            b64 = Base64("SGVsbG8=")
            b64 = b64.with_mime_type("text/plain")
            b64.mime_type
            # 'text/plain'
            ```
        """
        return Base64(self.data, mime_type=mime_type, encoding=self.encoding)

    @property
    def is_image(self) -> bool:
        """Checks if the data is an image.

        Returns:
            True if mime_type starts with 'image/', False otherwise.
        """
        return bool(self.mime_type and self.mime_type.startswith("image/"))

    @property
    def is_audio(self) -> bool:
        """Checks if the data is audio.

        Returns:
            True if mime_type starts with 'audio/', False otherwise.
        """
        return bool(self.mime_type and self.mime_type.startswith("audio/"))

    @property
    def is_video(self) -> bool:
        """Checks if the data is video.

        Returns:
            True if mime_type starts with 'video/', False otherwise.
        """
        return bool(self.mime_type and self.mime_type.startswith("video/"))

    @property
    def is_text(self) -> bool:
        """Checks if the data is text.

        Returns:
            True if mime_type starts with 'text/', False otherwise.
        """
        return bool(self.mime_type and self.mime_type.startswith("text/"))

    @property
    def is_json(self) -> bool:
        """Checks if the data is JSON.

        Returns:
            True if mime_type is 'application/json', False otherwise.
        """
        return self.mime_type == "application/json"

    @property
    def is_pdf(self) -> bool:
        """Checks if the data is PDF.

        Returns:
            True if mime_type is 'application/pdf', False otherwise.
        """
        return self.mime_type == "application/pdf"

    @property
    def encoded_size(self) -> int:
        """Returns the size of the Base64-encoded data in bytes.

        Returns:
            Length of the Base64 string.
        """
        return len(self.data)

    @property
    def decoded_size(self) -> int:
        """Returns the estimated size of the decoded data in bytes.

        This is an estimate based on the Base64 string length, which may
        differ slightly from the actual decoded size due to padding.

        Returns:
            Estimated decoded size in bytes.
        """
        # Base64 encodes 3 bytes into 4 characters
        # Remove padding characters for accurate calculation
        data_without_padding = self.data.rstrip("=")
        return (len(data_without_padding) * 3) // 4

    def truncate(self, max_length: int, suffix: str = "...") -> str:
        """Returns a truncated string representation for display.

        Args:
            max_length: Maximum length of the returned string.
            suffix: Suffix to append if truncated. Defaults to '...'.

        Returns:
            Truncated string.

        Examples:
            ```python
            b64 = Base64("SGVsbG8sIFdvcmxkIQ==")
            b64.truncate(10)
            # 'SGVsbG8...'
            ```
        """
        if len(self.data) <= max_length:
            return self.data
        return self.data[: max_length - len(suffix)] + suffix
