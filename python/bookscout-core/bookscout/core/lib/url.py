# pylint: disable=too-many-lines
from __future__ import annotations

import typing as t
from urllib.parse import parse_qs
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import urlparse
from urllib.parse import urlunparse


class URL(str):
    """A pathlib-like class for URL manipulation.

    This class provides a fluent interface for constructing and manipulating
    URLs, similar to how pathlib.Path works for filesystem paths. It supports
    protocol switching, path joining, query parameter management, and
    type-specific URL variants.

    **Design Philosophy:**
        - This is a URL **builder**, not a strict RFC validator
        - Immutable: all operations return new instances
        - Path segments with / are treated as multi-level paths
        - Query parameters preserve multi-value semantics

    Attributes:
        scheme: The URL scheme/protocol (e.g., 'http', 'https', 'ws', 'wss'),
            or empty string.
        netloc: The network location (e.g., 'example.com:8080').
        path: The URL path (e.g., '/api/v1/users').
        params: URL parameters (rarely used, for RFC 1808 compatibility).
        query: The query string (e.g., 'key=value&foo=bar').
        fragment: The URL fragment/anchor (e.g., 'section-1').

    Examples:
        ```python
        # Normal path joining - / is preserved as path separator
        url = URL("https://api.example.com") / "v1/users/123"
        str(url)
        # 'https://api.example.com/v1/users/123'

        # When you need to escape / (e.g., S3 object keys, file paths)
        url = URL("https://cdn.example.com/bucket").segment("folder/file.txt")
        str(url)
        # 'https://cdn.example.com/bucket/folder%2Ffile.txt'

        # Special characters are still encoded
        url = URL("https://cdn.example.com") / "files" / "hello world.pdf"
        str(url)
        # 'https://cdn.example.com/files/hello%20world.pdf'
        ```
    """

    __slots__ = ("fragment", "netloc", "params", "path", "query", "scheme")

    scheme: str
    netloc: str
    path: str
    params: str
    query: str
    fragment: str

    def __new__(
        cls,
        url: str | URL | None = None,
        *,
        scheme: str = "",  # noqa: ARG004
        netloc: str = "",  # noqa: ARG004
        path: str = "",  # noqa: ARG004
        params: str = "",  # noqa: ARG004
        query: str = "",  # noqa: ARG004
        fragment: str = "",  # noqa: ARG004
    ) -> t.Self:
        if isinstance(url, URL):
            s = str(url)
        elif isinstance(url, str):
            s = url
        else:
            s = ""

        return super().__new__(cls, s)

    def __init__(
        self,
        url: str | URL | None = None,
        *,
        scheme: str = "",
        netloc: str = "",
        path: str = "",
        params: str = "",
        query: str = "",
        fragment: str = "",
    ):
        """Initializes a URL instance.

        Args:
            url: A URL string or URL instance to parse. If provided, other
                parameters are ignored unless they need to override parsed
                values.
            scheme: The URL scheme/protocol. Defaults to empty string (no
                scheme).
            netloc: The network location. Defaults to empty string.
            path: The URL path. Defaults to empty string.
            params: URL parameters. Defaults to empty string.
            query: The query string. Defaults to empty string.
            fragment: The URL fragment. Defaults to empty string.
        """
        if isinstance(url, URL):
            self.scheme = url.scheme
            self.netloc = url.netloc
            self.path = url.path
            self.params = url.params
            self.query = url.query
            self.fragment = url.fragment
        elif isinstance(url, str):
            parsed = urlparse(url)

            # Handle cases where urlparse misinterprets netloc as path
            # e.g., "api.example.com/v1" -> netloc='', path='api.example.com/v1'
            if not parsed.scheme and not parsed.netloc and parsed.path:
                # Check if path looks like it contains a host (has dots and optional port)
                # and doesn't start with / (which would be a pure path)
                path_part = parsed.path
                if "/" in path_part:
                    potential_host, rest_path = path_part.split("/", 1)
                    rest_path = "/" + rest_path
                else:
                    potential_host = path_part
                    rest_path = ""

                # Heuristic: if it looks like a host (contains . or : ), treat as netloc
                if "." in potential_host or ":" in potential_host:
                    self.scheme = scheme
                    self.netloc = potential_host
                    self.path = rest_path or path
                    self.params = parsed.params or params
                    self.query = parsed.query or query
                    self.fragment = parsed.fragment or fragment
                else:
                    # Pure path
                    self.scheme = parsed.scheme or scheme
                    self.netloc = parsed.netloc or netloc
                    self.path = parsed.path or path
                    self.params = parsed.params or params
                    self.query = parsed.query or query
                    self.fragment = parsed.fragment or fragment
            else:
                # Normal case: urlparse did the right thing
                self.scheme = parsed.scheme or scheme
                self.netloc = parsed.netloc or netloc
                self.path = parsed.path or path
                self.params = parsed.params or params
                self.query = parsed.query or query
                self.fragment = parsed.fragment or fragment
        else:
            self.scheme = scheme
            self.netloc = netloc
            self.path = path
            self.params = params
            self.query = query
            self.fragment = fragment

    def __truediv__(self, other: str | URL) -> URL:
        """Joins URL paths using the / operator.

        Path segments containing / are treated as multi-level paths.
        Special characters (except /) are automatically URL-encoded.

        For cases where you need to escape / (e.g., S3 keys, file paths),
        use .segment() method instead.

        Args:
            other: A path segment string or another URL to join.

        Returns:
            A new URL instance with the joined path.

        Examples:
            ```python
            # Normal multi-level path joining
            url = URL("https://api.example.com") / "v1/users/123"
            str(url)
            # 'https://api.example.com/v1/users/123'

            # Special characters are encoded
            url = URL("https://cdn.example.com") / "files/hello world.pdf"
            str(url)
            # 'https://cdn.example.com/files/hello%20world.pdf'

            # Chain multiple segments
            url = URL("https://api.example.com") / "v1" / "users" / "123"
            str(url)
            # 'https://api.example.com/v1/users/123'
            ```
        """
        if isinstance(other, URL):
            other = other.path

        # URL-encode the path segment but KEEP / as path separator
        # safe="/" means / won't be encoded
        other = quote(str(other).lstrip("/"), safe="/")

        new_path = f"/{other}" if not self.path or self.path == "/" else f"{self.path.rstrip('/')}/{other}"

        return URL(
            scheme=self.scheme,
            netloc=self.netloc,
            path=new_path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def segment(self, segment: str, *, escape_slash: bool = True) -> URL:
        """Adds a path segment with full control over / escaping.

        This method is useful for scenarios where the segment itself may contain
        slashes that should be treated as part of the segment name (not as path
        separators), such as:
        - S3 object keys: "folder/file.txt"
        - File system paths: "C:/Users/name"
        - Any identifier containing slashes

        Args:
            segment: The path segment to add.
            escape_slash: If True, escapes / in the segment. If False, behaves
                like the / operator. Defaults to True.

        Returns:
            A new URL instance with the segment added.

        Examples:
            ```python
            # Escape slashes (default)
            url = URL("https://s3.amazonaws.com/bucket").segment("folder/file.txt")
            str(url)
            # 'https://s3.amazonaws.com/bucket/folder%2Ffile.txt'

            # Don't escape slashes (same as / operator)
            url = URL("https://api.com").segment("v1/users", escape_slash=False)
            str(url)
            # 'https://api.com/v1/users'

            # Real-world S3 example
            bucket_url = URL("https://s3.amazonaws.com/my-bucket")
            object_url = bucket_url.segment("uploads/2024/01/document.pdf")
            str(object_url)
            # 'https://s3.amazonaws.com/my-bucket/uploads%2F2024%2F01%2Fdocument.pdf'
            ```
        """
        if not escape_slash:
            return self / segment

        # Escape everything including /
        encoded_segment = quote(str(segment).lstrip("/"), safe="")

        new_path = (
            f"/{encoded_segment}" if not self.path or self.path == "/" else f"{self.path.rstrip('/')}/{encoded_segment}"
        )

        return URL(
            scheme=self.scheme,
            netloc=self.netloc,
            path=new_path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def segments(self, *segments: str, escape_slash: bool = False) -> URL:
        """Adds multiple path segments at once.

        Args:
            *segments: Path segments to add.
            escape_slash: If True, escapes / in each segment. If False,
                treats / as path separator. Defaults to False.

        Returns:
            A new URL instance with all segments added.

        Examples:
            ```python
            # Add multiple segments (default: / is separator)
            url = URL("https://api.example.com").segments("v1", "users", "123")
            str(url)
            # 'https://api.example.com/v1/users/123'

            # Escape slashes in each segment
            url = URL("https://cdn.com").segments("folder/1", "file/2", escape_slash=True)
            str(url)
            # 'https://cdn.com/folder%2F1/file%2F2'

            # Mix with / operator
            url = URL("https://api.com") / "v1"
            url = url.segments("users", "active", "123")
            str(url)
            # 'https://api.com/v1/users/active/123'
            ```
        """
        result = self
        for seg in segments:
            result = result.segment(seg, escape_slash=escape_slash)
        return result

    def __str__(self) -> str:
        """Converts the URL to its string representation.

        If scheme is empty, returns URL without scheme prefix.

        Returns:
            The complete URL as a string.

        Examples:
            ```python
            url = URL("https://example.com/path")
            str(url)
            # 'https://example.com/path'

            url = URL("example.com/path")
            str(url)
            # 'example.com/path'

            url = URL("//example.com/path")
            str(url)
            # '//example.com/path'
            ```
        """
        if not self.scheme:
            # Build URL without scheme
            result = self.netloc
            if self.path:
                result += self.path
            if self.params:
                result += f";{self.params}"
            if self.query:
                result += f"?{self.query}"
            if self.fragment:
                result += f"#{self.fragment}"
            return result

        return urlunparse((  # type: ignore[return-value]
            self.scheme,
            self.netloc,
            self.path,
            self.params,
            self.query,
            self.fragment,
        ))

    def __repr__(self) -> str:
        """Returns a detailed string representation of the URL.

        Returns:
            String in format "URL('scheme://netloc/path?query#fragment')".
        """
        return f"{self.__class__.__name__}('{self!s}')"

    def __eq__(self, other: object) -> bool:
        """Compares two URLs for equality.

        Note: This compares URLs as value objects (exact string match).
        URLs that are semantically equivalent but syntactically different
        will NOT be equal (e.g., 'http://a.com' != 'http://a.com/').
        Use .normalize() before comparison if you need semantic equality.

        Args:
            other: Another URL instance or string to compare with.

        Returns:
            True if the URLs are equal, False otherwise.
        """
        if isinstance(other, str):
            other = URL(other)
        elif not isinstance(other, URL):
            return NotImplemented

        return (
            self.scheme == other.scheme
            and self.netloc == other.netloc
            and self.path == other.path
            and self.params == other.params
            and self.query == other.query
            and self.fragment == other.fragment
        )

    def __hash__(self) -> int:
        """Returns the hash of the URL.

        Returns:
            Hash value of the URL string.
        """
        return hash(str(self))

    def __reduce__(self) -> tuple[type[URL], tuple[str]]:
        """Support for pickling the URL instance.

        Returns:
            A tuple containing the class and its initialization arguments.
        """
        return self.__class__, (str(self),)

    def __getnewargs_ex__(self) -> tuple[tuple[str], dict[str, t.Any]]:
        """Support for pickling with __getnewargs_ex__.

        Returns:
            A tuple containing positional and keyword arguments for
            reconstruction.
        """
        return (str(self),), {}

    def __getstate__(self) -> None:
        """Support for pickling the URL instance.

        Returns:
            None, as all state is captured in __reduce__.
        """
        return

    def __setstate__(self, state: None) -> None:
        """Support for unpickling the URL instance.

        Args:
            state: The state to restore (None in this case).
        """

    def __copy__(self) -> URL:
        """Creates a shallow copy of the URL instance.

        Returns:
            A new URL instance with the same values.
        """
        return URL(
            scheme=self.scheme,
            netloc=self.netloc,
            path=self.path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def __deepcopy__(self, memo: dict[int, t.Any]) -> URL:
        """Creates a deep copy of the URL instance.

        Args:
            memo: A dictionary to track already copied objects.

        Returns:
            A new URL instance with the same values.
        """
        return self.__copy__()

    def __bool__(self) -> bool:
        """Determines the truthiness of the URL instance.

        A URL is considered True if it has a non-empty netloc or path.

        Returns:
            True if the URL has a netloc or path, False otherwise.
        """
        return bool(self.netloc or self.path)

    def __len__(self) -> int:
        """Returns the length of the URL string.

        Returns:
            The length of the URL when converted to a string.
        """
        return len(str(self))

    def __contains__(self, item: object) -> bool:  # type: ignore[override]
        """Checks if a substring is present in the URL string.

        Args:
            item: The substring to check for.

        Returns:
            True if the substring is found, False otherwise.
        """
        return isinstance(item, str) and item in str(self)

    def __format__(self, format_spec: str) -> str:
        """Formats the URL instance according to the given format specification.

        Args:
            format_spec: The format specification string.

        Returns:
            The formatted URL string.
        """
        return format(str(self), format_spec)

    def with_scheme(self, scheme: str) -> URL:
        """Returns a new URL with a different scheme.

        Args:
            scheme: The new scheme/protocol (e.g., 'https', 'wss'), or empty
                string for no scheme.

        Returns:
            A new URL instance with the updated scheme.

        Examples:
            ```python
            url = URL("http://example.com")
            url = url.with_scheme("https")
            str(url)
            # 'https://example.com'

            url = url.with_scheme("")
            str(url)
            # '//example.com'
            ```
        """
        return URL(
            scheme=scheme,
            netloc=self.netloc,
            path=self.path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def with_netloc(self, netloc: str) -> URL:
        """Returns a new URL with a different network location.

        Args:
            netloc: The new network location (e.g., 'example.com:8080').

        Returns:
            A new URL instance with the updated netloc.
        """
        return URL(
            scheme=self.scheme,
            netloc=netloc,
            path=self.path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def with_path(self, path: str) -> URL:
        """Returns a new URL with a different path.

        Args:
            path: The new path (e.g., '/api/v2/users').

        Returns:
            A new URL instance with the updated path.
        """
        return URL(
            scheme=self.scheme,
            netloc=self.netloc,
            path=path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def replace_query(self, query: str | dict[str, t.Any] | None = None, **kwargs: t.Any) -> URL:
        """Returns a new URL with query parameters REPLACED (not merged).

        **Warning:** This completely replaces the existing query string.
        If you want to merge/update parameters, use .merge_query() instead.

        Args:
            query: Either a query string, a dictionary of parameters, or None.
                If None, uses kwargs as parameters.
            **kwargs: Query parameters as keyword arguments. Only used if query
                is None.

        Returns:
            A new URL instance with the replaced query string.

        Examples:
            ```python
            url = URL("https://api.example.com/users?page=1")
            url = url.replace_query(limit=10)
            str(url)
            # 'https://api.example.com/users?limit=10'  # page=1 is gone!
            ```
        """
        if query is None:
            query = kwargs

        query_str = urlencode(query, doseq=True) if isinstance(query, dict) else str(query)

        return URL(
            scheme=self.scheme,
            netloc=self.netloc,
            path=self.path,
            params=self.params,
            query=query_str,
            fragment=self.fragment,
        )

    def merge_query(self, **kwargs: t.Any) -> URL:
        """Returns a new URL with query parameters merged/updated.

        This method preserves existing query parameters and updates or adds
        new ones based on the provided keyword arguments.

        Args:
            **kwargs: Query parameters to add or update.

        Returns:
            A new URL instance with the merged query parameters.

        Examples:
            ```python
            url = URL("https://api.example.com/users?page=1")
            url = url.merge_query(limit=10, sort="name")
            str(url)
            # 'https://api.example.com/users?page=1&limit=10&sort=name'
            ```
        """
        current_query = self.query_params_dict

        # Merge new params, converting single values to lists for consistency
        for key, value in kwargs.items():
            if isinstance(value, list):
                current_query[key] = value
            else:
                current_query[key] = [str(value)]

        return self.replace_query(current_query)

    def with_fragment(self, fragment: str) -> URL:
        """Returns a new URL with a different fragment.

        Args:
            fragment: The new fragment/anchor (e.g., 'section-1').

        Returns:
            A new URL instance with the updated fragment.
        """
        return URL(
            scheme=self.scheme,
            netloc=self.netloc,
            path=self.path,
            params=self.params,
            query=self.query,
            fragment=fragment,
        )

    @property
    def query_params(self) -> dict[str, list[str]]:
        """Parses and returns the query parameters as a dictionary.

        **Always returns lists for values**, even for single-value parameters.
        This preserves multi-value semantics and prevents silent data loss.

        Returns:
            Dictionary mapping parameter names to lists of values.

        Examples:
            ```python
            url = URL("https://api.example.com?a=1&a=2&b=3")
            url.query_params
            # {'a': ['1', '2'], 'b': ['3']}
            ```
        """
        if not self.query:
            return {}

        return parse_qs(self.query, keep_blank_values=True)

    @property
    def query_params_dict(self) -> dict[str, list[str]]:
        """Alias for query_params (explicit name for clarity)."""
        return self.query_params

    @property
    def query_params_flat(self) -> dict[str, str]:
        """Returns query parameters with single values flattened.

        Multi-value parameters will only return the LAST value.
        Use this only when you're certain parameters are single-valued.

        Returns:
            Dictionary mapping parameter names to single string values.

        Examples:
            ```python
            url = URL("https://api.example.com?a=1&a=2&b=3")
            url.query_params_flat
            # {'a': '2', 'b': '3'}  # Note: only last 'a' value kept
            ```
        """
        return {k: v[-1] for k, v in self.query_params.items()}

    @property
    def host(self) -> str:
        """Extracts the host from the netloc (without port).

        Correctly handles IPv6 addresses in bracket notation.

        Returns:
            The host portion of the netloc.

        Examples:
            ```python
            URL("http://example.com:8080/path").host
            # 'example.com'

            URL("http://[2001:db8::1]:8080/path").host
            # '2001:db8::1'
            ```
        """
        if not self.netloc:
            return ""

        # Handle IPv6: [2001:db8::1]:8080
        if self.netloc.startswith("["):
            # Find closing bracket
            bracket_end = self.netloc.find("]")
            if bracket_end != -1:
                return self.netloc[1:bracket_end]

        # Handle regular host:port
        if ":" in self.netloc:
            # Check if it's IPv6 without brackets (malformed but handle gracefully)
            if self.netloc.count(":") > 1:
                # Multiple colons = likely bare IPv6
                return self.netloc
            return self.netloc.rsplit(":", 1)[0]

        return self.netloc

    @property
    def port(self) -> int | None:
        """Extracts the port from the netloc.

        Correctly handles IPv6 addresses in bracket notation.

        Returns:
            The port number as an integer, or None if not specified.

        Examples:
            ```python
            URL("http://example.com:8080/path").port
            # 8080

            URL("http://[2001:db8::1]:8080/path").port
            # 8080

            URL("http://example.com/path").port
            # None
            ```
        """
        if not self.netloc:
            return None

        # Handle IPv6: [2001:db8::1]:8080
        if self.netloc.startswith("["):
            bracket_end = self.netloc.find("]")
            if bracket_end != -1 and bracket_end + 1 < len(self.netloc):
                port_part = self.netloc[bracket_end + 1 :]
                if port_part.startswith(":"):
                    try:
                        return int(port_part[1:])
                    except ValueError:
                        return None
            return None

        # Handle regular host:port
        if ":" in self.netloc:
            # Check if it's bare IPv6 (no port)
            if self.netloc.count(":") > 1:
                return None
            try:
                return int(self.netloc.rsplit(":", 1)[1])
            except ValueError:
                return None

        return None

    def is_ws(self) -> bool:
        """Checks if the URL uses WebSocket schemes (ws or wss).

        Returns:
            True if the scheme is 'ws' or 'wss', False otherwise.
        """
        return self.scheme.lower() in {"ws", "wss"}

    def is_http(self) -> bool:
        """Checks if the URL uses HTTP schemes (http or https).

        Returns:
            True if the scheme is 'http' or 'https', False otherwise.
        """
        return self.scheme.lower() in {"http", "https"}

    def is_secure(self) -> bool:
        """Checks if the URL uses secure schemes (https or wss).

        Returns:
            True if the scheme is 'https' or 'wss', False otherwise.
        """
        return self.scheme.lower() in {"https", "wss"}

    def normalize(self) -> URL:
        """Returns a normalized version of the URL.

        Normalization includes:
            - Lowercasing the scheme
            - Removing default ports (80 for http, 443 for https)
            - Collapsing redundant slashes in path (//)
            - Ensuring non-empty path for URLs with netloc
            - Lowercasing the host (but not the path)

        This is useful for:
            - Cache key generation
            - URL deduplication
            - Canonical URL comparison

        Returns:
            A new normalized URL instance.

        Examples:
            ```python
            url = URL("HTTP://Example.com:80//api//users")
            url = url.normalize()
            str(url)
            # 'http://example.com/api/users'
            ```
        """
        scheme = self.scheme.lower() if self.scheme else ""
        netloc = self.netloc
        path = self.path

        # Lowercase host but preserve port
        if netloc:
            if ":" in netloc and not netloc.startswith("["):
                host, port = netloc.rsplit(":", 1)
                netloc = f"{host.lower()}:{port}"
            else:
                netloc = netloc.lower()

        # Remove default ports
        if scheme == "http" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif scheme == "https" and netloc.endswith(":443"):
            netloc = netloc[:-4]
        elif scheme == "ws" and netloc.endswith(":80"):
            netloc = netloc[:-3]
        elif scheme == "wss" and netloc.endswith(":443"):
            netloc = netloc[:-4]

        # Collapse redundant slashes in path
        if path:
            leading_slash = path.startswith("/")
            segments = [seg for seg in path.split("/") if seg]
            path = "/".join(segments)
            if leading_slash:
                path = "/" + path

        # Ensure path is "/" if netloc exists but path is empty
        if netloc and not path:
            path = "/"

        return URL(
            scheme=scheme,
            netloc=netloc,
            path=path,
            params=self.params,
            query=self.query,
            fragment=self.fragment,
        )

    def as_http(self) -> HttpURL:
        """Converts the URL to an HTTP URL.

        Returns:
            A new HttpURL instance with scheme set to 'http'.
        """
        return HttpURL(self.with_scheme("http"))

    def as_https(self) -> HttpsURL:
        """Converts the URL to an HTTPS URL.

        Returns:
            A new HttpsURL instance with scheme set to 'https'.
        """
        return HttpsURL(self.with_scheme("https"))

    def as_ws(self) -> WsURL:
        """Converts the URL to a WebSocket URL.

        Returns:
            A new WsURL instance with scheme set to 'ws'.
        """
        return WsURL(self.with_scheme("ws"))

    def as_wss(self) -> WssURL:
        """Converts the URL to a secure WebSocket URL.

        Returns:
            A new WssURL instance with scheme set to 'wss'.
        """
        return WssURL(self.with_scheme("wss"))


class HttpURL(URL):
    """HTTP-specific URL class.

    This class represents HTTP URLs and ensures the scheme is always 'http'.
    It provides the same interface as URL but is specialized for HTTP protocol.

    Examples:
        ```python
        url = HttpURL("//example.com/api")
        str(url)
        # 'http://example.com/api'
        ```
    """

    def __init__(
        self,
        url: str | URL | None = None,
        *,
        netloc: str = "",
        path: str = "",
        params: str = "",
        query: str = "",
        fragment: str = "",
    ):
        """Initializes an HTTP URL.

        Args:
            url: A URL string or URL instance to parse.
            netloc: The network location.
            path: The URL path.
            params: URL parameters.
            query: The query string.
            fragment: The URL fragment.
        """
        super().__init__(
            url,
            scheme="http",
            netloc=netloc,
            path=path,
            params=params,
            query=query,
            fragment=fragment,
        )
        self.scheme = "http"


class HttpsURL(URL):
    """HTTPS-specific URL class.

    This class represents HTTPS URLs and ensures the scheme is always 'https'.
    It provides the same interface as URL but is specialized for HTTPS protocol.

    Examples:
        ```python
        url = HttpsURL("//example.com/api")
        str(url)
        # 'https://example.com/api'
        ```
    """

    def __init__(
        self,
        url: str | URL | None = None,
        *,
        netloc: str = "",
        path: str = "",
        params: str = "",
        query: str = "",
        fragment: str = "",
    ):
        """Initializes an HTTPS URL.

        Args:
            url: A URL string or URL instance to parse.
            netloc: The network location.
            path: The URL path.
            params: URL parameters.
            query: The query string.
            fragment: The URL fragment.
        """
        super().__init__(
            url,
            scheme="https",
            netloc=netloc,
            path=path,
            params=params,
            query=query,
            fragment=fragment,
        )
        self.scheme = "https"


class WsURL(URL):
    """WebSocket-specific URL class.

    This class represents WebSocket URLs and ensures the scheme is always 'ws'.
    It provides the same interface as URL but is specialized for WebSocket
    protocol.

    Examples:
        ```python
        url = WsURL("//example.com/socket")
        str(url)
        # 'ws://example.com/socket'
        ```
    """

    def __init__(
        self,
        url: str | URL | None = None,
        *,
        netloc: str = "",
        path: str = "",
        params: str = "",
        query: str = "",
        fragment: str = "",
    ):
        """Initializes a WebSocket URL.

        Args:
            url: A URL string or URL instance to parse.
            netloc: The network location.
            path: The URL path.
            params: URL parameters.
            query: The query string.
            fragment: The URL fragment.
        """
        super().__init__(
            url,
            scheme="ws",
            netloc=netloc,
            path=path,
            params=params,
            query=query,
            fragment=fragment,
        )
        self.scheme = "ws"


class WssURL(URL):
    """Secure WebSocket-specific URL class.

    This class represents secure WebSocket URLs and ensures the scheme is
    always 'wss'. It provides the same interface as URL but is specialized for
    secure WebSocket protocol.

    Examples:
        ```python
        url = WssURL("//example.com/socket")
        str(url)
        # 'wss://example.com/socket'
        ```
    """

    def __init__(
        self,
        url: str | URL | None = None,
        *,
        netloc: str = "",
        path: str = "",
        params: str = "",
        query: str = "",
        fragment: str = "",
    ):
        """Initializes a secure WebSocket URL.

        Args:
            url: A URL string or URL instance to parse.
            netloc: The network location.
            path: The URL path.
            params: URL parameters.
            query: The query string.
            fragment: The URL fragment.
        """
        super().__init__(
            url,
            scheme="wss",
            netloc=netloc,
            path=path,
            params=params,
            query=query,
            fragment=fragment,
        )
        self.scheme = "wss"
