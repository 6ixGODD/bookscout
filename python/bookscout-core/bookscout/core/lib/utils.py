from __future__ import annotations

import base64
import datetime
import functools
import hashlib
import inspect
import mimetypes
import secrets
import string
import typing as t
import uuid
import weakref


def gen_id(prefix: str = "", suffix: str = "", without_hyphen: bool = True) -> str:
    """Generate a unique identifier (UUID) with optional prefix and suffix.

    Args:
        prefix: A string to prepend to the generated UUID (default: "").
        suffix: A string to append to the generated UUID (default: "").
        without_hyphen: Whether to remove hyphens from the UUID
            (default: True).

    Returns:
        A unique identifier string with the specified prefix and suffix.
    """
    uuid_str = str(uuid.uuid4())
    if without_hyphen:
        uuid_str = uuid_str.replace("-", "")
    return f"{prefix}{uuid_str}{suffix}"


def gen_secret(length: int = 32, /, prefix: str = "", suffix: str = "") -> str:
    """Generate a secure random secret key.

    Args:
        length: The length of the generated secret key (default: 32).
        prefix: A string to prepend to the generated key (default: "").
        suffix: A string to append to the generated key (default: "").

    Returns:
        A securely generated random secret key as a string.
    """
    secret = secrets.token_urlsafe(length)
    return f"{prefix}{secret}{suffix}"


def utcnow() -> datetime.datetime:
    """Get the current UTC datetime with timezone info.

    Returns:
        The current UTC datetime with timezone info.
    """
    return datetime.datetime.now(tz=datetime.UTC)


def utcnow_ts() -> float:
    """Get the current UTC timestamp as a float.

    Returns:
        The current UTC timestamp as a float.
    """
    return utcnow().timestamp()


T = t.TypeVar("T")


class classproperty(property, t.Generic[T]):
    """A descriptor that behaves like @property but for class methods.
    Allows defining read-only properties at the class level.

    Examples:
        ```python
        class MyClass:
            _value = 42

            @classproperty
            def value(cls):
                return cls._value


        print(MyClass.value)  # Outputs: 42
        ```
    """

    def __init__(self, fget: t.Callable[[type], T]) -> None:
        super().__init__(fget)

    def __get__(self, instance: t.Any, owner: type | None = None) -> T:  # type: ignore[override]
        resolved_owner = owner if owner is not None else type(instance)
        return t.cast(t.Callable[[type], T], self.fget)(resolved_owner)


@functools.cache
def _get_func_params(fn: t.Callable[..., t.Any]) -> set[str]:
    return set(inspect.signature(fn).parameters.keys())


def filter_kwargs(
    *fn: t.Callable[..., t.Any],
    kwargs: dict[str, t.Any],
    pref: str = "",
) -> dict[str, t.Any]:
    """Filter out invalid keyword arguments for a given function by comparing
    the provided keyword arguments to the function's signature. Only valid
    keyword arguments are returned.

    Args:
        *fn: The functions to filter keyword arguments for.
        kwargs: The keyword arguments to filter.
        pref: The prefix to remove from keyword argument names before
            checking. Defaults to "".

    Returns:
        The filtered keyword arguments with valid parameter names only.
    """
    valid_params = set()
    for f in fn:
        valid_params.update(_get_func_params(f))  # type: ignore

    if pref:
        # Remove prefix and filter
        filtered = {}
        for key, value in kwargs.items():
            if key.startswith(pref):
                param_name = key[len(pref) :]
                if param_name in valid_params and param_name not in {"self", "cls"}:
                    filtered[param_name] = value
        return filtered
    # Direct filtering without prefix removal
    return {key: value for key, value in kwargs.items() if key in valid_params and key not in {"self", "cls"}}


def flatten_dict(
    m: t.Mapping[str, t.Any],
    /,
    sep: str = ".",
    _parent: str = "",
) -> dict[str, t.Any]:
    """Flatten a nested dictionary into a single-level dictionary with
    dot-separated keys.

    Args:
        m: The nested dictionary to flatten.
        sep: The separator to use between keys (default: '.').
        _parent: The parent key prefix (used for recursion).

    Returns:
        A flattened dictionary with dot-separated keys.
    """
    items = []  # type: list[tuple[str, t.Any]]
    for k, v in m.items():
        key = f"{_parent}{sep}{k}" if _parent else k
        if isinstance(v, t.Mapping):
            items.extend(flatten_dict(v, _parent=key, sep=sep).items())
        else:
            items.append((key, v))
    return dict(items)


def int_to_base64url(value: int, /) -> str:
    """Convert an integer to a URL-safe base64-encoded string without padding.

    Args:
        value: The integer to convert.

    Returns:
        A URL-safe base64-encoded string representation of the integer.
    """
    # Convert integer to bytes
    byte_length = (value.bit_length() + 7) // 8
    value_bytes = value.to_bytes(byte_length, byteorder="big")

    encoded = base64.urlsafe_b64encode(value_bytes).decode("ascii")
    return encoded.rstrip("=")


def secure_compare(a: str, b: str, /) -> bool:
    """Perform a constant-time comparison between two strings.

    This function compares two strings in a way that is resistant to timing
    attacks. It ensures that the time taken to compare the strings does not
    depend on their content, making it more secure for sensitive data
    comparisons.

    Args:
        a: The first string to compare.
        b: The second string to compare.

    Returns:
        True if the strings are equal, False otherwise.
    """
    if len(a) != len(b):
        return False

    result = 0
    for x, y in zip(a.encode(), b.encode(), strict=True):
        result |= x ^ y

    return result == 0


def gencode(
    length: int = 6,
    *,
    digits: bool = True,
    uppercase: bool = False,
    lowercase: bool = False,
    symbols: bool = False,
    exclude_similar: bool = True,
    prefix: str = "",
    suffix: str = "",
) -> str:
    """Generate a secure random code with customizable character sets.

    Args:
        length: The length of the generated code (default: 6).
        digits: Whether to include digits (0-9) in the code (default: True).
        uppercase: Whether to include uppercase letters (A-Z) in the code
            (default: False).
        lowercase: Whether to include lowercase letters (a-z) in the code
            (default: False).
        symbols: Whether to include special symbols (!@#$%^&*) in the code
            (default: False).
        exclude_similar: Whether to exclude similar-looking characters
            (i.e., 'i', 'l', '1', 'L', 'o', '0', 'O') from the code
            (default: True).
        prefix: A string to prepend to the generated code (default: '').
        suffix: A string to append to the generated code (default: '').

    Returns:
        A securely generated random code as a string.
    """
    charset = ""
    if digits:
        charset += string.digits
    if uppercase:
        charset += string.ascii_uppercase
    if lowercase:
        charset += string.ascii_lowercase
    if symbols:
        charset += "!@#$%^&*"

    if exclude_similar:
        similar_chars = "il1Lo0O"
        charset = "".join(c for c in charset if c not in similar_chars)
    if not charset:
        raise ValueError("At least one character set must be selected.")

    code = "".join(secrets.choice(charset) for _ in range(length))
    return f"{prefix}{code}{suffix}"


async def aenumerate[_T](  # noqa: UP049
    iterable: t.AsyncIterable[_T],
    /,
    start: int = 0,
) -> t.AsyncIterator[tuple[int, _T]]:
    """Asynchronous version of enumerate.

    Args:
        iterable: An asynchronous iterable to enumerate.
        start: The starting index (default: 0).

    Yields:
        Tuples of (index, item) from the asynchronous iterable.
    """
    index = start
    async for item in iterable:
        yield index, item
        index += 1


CACHE: weakref.WeakValueDictionary[t.Hashable, t.Any] = weakref.WeakValueDictionary()


_FactoryFunc = t.TypeVar("_FactoryFunc", bound=t.Callable[..., t.Any])


def cached_factory(  # noqa: UP047
    func: _FactoryFunc | None = None,
    *,
    key_params: tuple[str, ...] | None = None,
) -> _FactoryFunc | t.Callable[[_FactoryFunc], _FactoryFunc]:
    """Flexible caching decorator with configurable cache key strategy.

    Args:
        func: The factory function to cache
        key_params: Parameter names to include in cache key.
                   If None, only first positional arg is used.

    Example:
        # Only config affects cache
        @cached_factory
        def create_db(config: str, logger: Logger) -> DB:
            return DB(config)

        # Config and region affect cache
        @cached_factory(key_params=("config", "region"))
        def create_db(config: str, region: str, logger: Logger) -> DB:
            return DB(config, region)
    """

    def decorator(f: _FactoryFunc) -> _FactoryFunc:
        sig = inspect.signature(f)
        param_names = list(sig.parameters.keys())

        @functools.wraps(f)
        def wrapper(config: t.Hashable, /, *args: t.Any, **kwargs: t.Any) -> t.Any:
            if key_params is None:
                cache_key = config
            else:
                key_parts: list[t.Any] = [config]
                for i, param_name in enumerate(param_names[1:], 1):
                    if param_name in key_params:
                        if i - 1 < len(args):
                            key_parts.append(args[i - 1])
                        else:
                            key_parts.append(kwargs.get(param_name))
                cache_key = tuple(key_parts)

            if cache_key in CACHE:
                return CACHE[cache_key]

            result = f(config, *args, **kwargs)  # type: ignore[operator]
            CACHE[cache_key] = result
            return result

        return wrapper  # type: ignore[return-value]

    if func is None:
        return decorator
    return decorator(func)


def all_classes_in_hierarchy[_T](cls: type[_T]) -> list[type[_T]]:  # noqa: UP049
    """Return all subclasses of `cls`, including `cls` itself (recursive).

    Args:
        cls: The class to get subclasses for.

    Returns:
        A list of all subclasses of `cls`, including `cls` itself.
    """
    subclasses = {cls}
    for sub in cls.__subclasses__():
        subclasses |= set(all_classes_in_hierarchy(sub))
    return list(subclasses)


def sha256(data: bytes, /) -> str:
    """Compute the SHA-256 hash of the given data.

    Args:
        data (bytes): The data to hash.

    Returns:
        str: The hexadecimal representation of the SHA-256 hash.
    """
    return hashlib.sha256(data).hexdigest()


def ext_for_mime(mime: str) -> str:
    """Return the preferred extension for *mime*, e.g. ``'.pdf'``."""
    return mimetypes.guess_extension(mime) or ""


def fp(v: str) -> str:
    """Return a fingerprint of the given string, showing its length, a
    truncated SHA-256 hash, and the head and tail of the string for quick
    identification.

    Args:
        v: The string to fingerprint.

    Returns:
        str: The fingerprint of the string in the format
            "len={length} sha256={hash} head={head} tail={tail}".
    """
    return f"len={len(v)} sha256={hashlib.sha256(v.encode()).hexdigest()[:12]} head={v[:6]!r} tail={v[-4:]!r}"
