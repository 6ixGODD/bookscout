from __future__ import annotations

import builtins
import re
import typing as t


class Version(str):
    """A semantic versioning (SemVer) value object.

    This class provides a fluent interface for constructing, manipulating,
    and comparing semantic versions. It follows the Semantic Versioning 2.0.0
    specification (https://semver.org/).

    **Design Philosophy:**
        - Immutable: all operations return new instances
        - SemVer 2.0.0 compliant
        - Natural comparison operators (`<`, `>`, `==`, etc.)
        - Fluent API for version bumping and prerelease management

    Attributes:
        major: The major version number (breaking changes).
        minor: The minor version number (backwards-compatible features).
        patch: The patch version number (backwards-compatible bug fixes).
        prerelease: Prerelease identifiers (e.g., `'alpha.1'`, `'beta.2'`,
            `'rc.1'`).
        build: Build metadata (e.g., `'build.123'`, `'20230101'`).

    Version Format:
        `MAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]`

    Comparison Rules (SemVer 2.0.0):
        - Compare major, minor, patch numerically
        - Prerelease versions have LOWER precedence than stable
        - 1.0.0-alpha < 1.0.0
        - Prerelease identifiers are compared left-to-right
        - Build metadata is IGNORED in comparisons

    Examples:
        ```python
        v = Version("1.2.3")
        v = v.bump_minor()
        str(v)
        # '1.3.0'

        v = Version("2.0.0").as_beta(1)
        str(v)
        # '2.0.0-beta.1'

        v1 = Version("1.0.0-alpha")
        v2 = Version("1.0.0")
        v1 < v2
        # True

        # Short forms
        Version("1.2.3").short
        # 'v1.2'
        Version("2.0.0").major_only
        # 'v2'
        ```
    """

    __slots__ = ("build", "major", "minor", "patch", "prerelease")

    major: int
    minor: int
    patch: int
    prerelease: str
    build: str

    # SemVer regex pattern
    _SEMVER_PATTERN = re.compile(
        r"^[vV]?"  # Optional 'v' or 'V' prefix
        r"(?P<major>0|[1-9]\d*)"
        r"(?:\.(?P<minor>0|[1-9]\d*))?"  # Optional minor
        r"(?:\.(?P<patch>0|[1-9]\d*))?"  # Optional patch
        r"(?:-(?P<prerelease>[0-9A-Za-z\-.]+))?"  # Optional prerelease
        r"(?:\+(?P<build>[0-9A-Za-z\-.]+))?"  # Optional build metadata
        r"$"
    )

    def __new__(
        cls,
        version: str | Version | None,
        *,
        major: int = 0,  # noqa: ARG004
        minor: int = 0,  # noqa: ARG004
        patch: int = 0,  # noqa: ARG004
        prerelease: str = "",  # noqa: ARG004
        build: str = "",  # noqa: ARG004
    ) -> t.Self:
        if isinstance(version, Version):
            version_str = str(version)
        elif isinstance(version, str):
            version_str = version
        else:
            version_str = ""

        return super().__new__(cls, version_str)

    def __init__(
        self,
        version: str | Version | None = None,
        *,
        major: int = 0,
        minor: int = 0,
        patch: int = 0,
        prerelease: str = "",
        build: str = "",
    ) -> None:
        """Initializes a Version instance.

        Args:
            version: A version string or Version instance to parse. If provided,
                other parameters are ignored unless they need to override
                parsed values.
            major: The major version number. Defaults to 0.
            minor: The minor version number. Defaults to 0.
            patch: The patch version number. Defaults to 0.
            prerelease: Prerelease identifiers. Defaults to empty string.
            build: Build metadata. Defaults to empty string.

        Raises:
            ValueError: If the version string is invalid.

        Examples:
            ```python
            Version("1.2.3")
            Version("v2.0.0-alpha.1+build.123")
            Version(major=1, minor=0, patch=0)
            Version("1", minor=5)  # 1.5.0
            ```
        """
        if isinstance(version, Version):
            self.major = version.major
            self.minor = version.minor
            self.patch = version.patch
            self.prerelease = version.prerelease
            self.build = version.build
        elif isinstance(version, str):
            match = self._SEMVER_PATTERN.match(version)
            if not match:
                raise ValueError(
                    f"Invalid version string: '{version}'. Expected format: MAJOR[.MINOR[.PATCH]][-PRERELEASE][+BUILD]"
                )

            self.major = int(match.group("major"))
            self.minor = int(match.group("minor") or minor)
            self.patch = int(match.group("patch") or patch)
            self.prerelease = match.group("prerelease") or prerelease
            self.build = match.group("build") or build
        else:
            self.major = major
            self.minor = minor
            self.patch = patch
            self.prerelease = prerelease
            self.build = build

    def __str__(self) -> str:
        """Returns the canonical string representation.

        Returns:
            Version string in format: `vMAJOR.MINOR.PATCH[-PRERELEASE][+BUILD]`

        Examples:
            ```python
            str(Version("1.2.3"))
            # 'v1.2.3'
            str(Version("2.0.0-beta.1+build.456"))
            # 'v2.0.0-beta.1+build.456'
            ```
        """
        result = f"v{self.major}.{self.minor}.{self.patch}"

        if self.prerelease:
            result += f"-{self.prerelease}"

        if self.build:
            result += f"+{self.build}"

        return result

    def __repr__(self) -> str:
        """Returns a detailed string representation.

        Returns:
            String in format `'Version('v1.2.3')'`.
        """
        return f"{self.__class__.__name__}('{self!s}')"

    def __hash__(self) -> int:
        """Returns the hash of the version.

        Note: Build metadata is excluded from hash (per SemVer spec).

        Returns:
            Hash value of the version tuple.
        """
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __eq__(self, other: object) -> bool:
        """Compares two versions for equality.

        Per SemVer spec, build metadata is IGNORED in comparison.

        Args:
            other: Another Version instance or string to compare with.

        Returns:
            True if versions are equal, False otherwise.

        Examples:
            ```python
            Version("1.0.0") == Version("1.0.0+build.123")
            # True (build metadata ignored)
            Version("1.0.0") == Version("1.0.0-alpha")
            # False
            ```
        """
        if isinstance(other, str):
            other = Version(other)
        elif not isinstance(other, Version):
            return NotImplemented

        return (
            self.major == other.major
            and self.minor == other.minor
            and self.patch == other.patch
            and self.prerelease == other.prerelease
        )

    def __lt__(self, other: Version | str) -> bool:
        """Less than comparison.

        Follows SemVer 2.0.0 precedence rules:
            1. Compare major, minor, patch numerically
            2. Prerelease versions have LOWER precedence than stable
            3. Compare prerelease identifiers left-to-right

        Args:
            other: Another `Version` instance or string.

        Returns:
            `True` if self < other, `False` otherwise.

        Examples:
            ```python
            Version("1.0.0") < Version("2.0.0")
            # True
            Version("1.0.0-alpha") < Version("1.0.0")
            # True (prerelease < stable)
            Version("1.0.0-alpha") < Version("1.0.0-beta")
            # True
            ```
        """
        if isinstance(other, str):
            other = Version(other)
        elif not isinstance(other, Version):  # type: ignore[unreachable]
            return NotImplemented
        if (self.major, self.minor, self.patch) != (other.major, other.minor, other.patch):
            return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

        # If versions are equal, check prerelease
        # Stable version (no prerelease) > prerelease version
        if not self.prerelease and other.prerelease:
            return False  # Stable is greater
        if self.prerelease and not other.prerelease:
            return True  # Prerelease is less

        # Both have prerelease, compare them
        if self.prerelease and other.prerelease:
            return self._compare_prerelease(self.prerelease, other.prerelease) < 0

        return False

    def __le__(self, other: Version | str) -> bool:
        """Less than or equal comparison."""
        return self == other or self < other

    def __gt__(self, other: Version | str) -> bool:
        """Greater than comparison."""
        if isinstance(other, str):
            other = Version(other)
        return self > other

    def __ge__(self, other: Version | str) -> bool:
        """Greater than or equal comparison."""
        return not self < other

    @staticmethod
    def _compare_prerelease(pre1: str, pre2: str) -> int:
        """Compares two prerelease strings according to SemVer rules.

        Args:
            pre1: First prerelease string.
            pre2: Second prerelease string.

        Returns:
            `-1` if `pre1 < pre2`, `0` if `equal`, `1` if `pre1 > pre2`.
        """
        parts1 = pre1.split(".")
        parts2 = pre2.split(".")

        for p1, p2 in zip(parts1, parts2, strict=False):
            # Numeric identifiers are compared as integers
            n1 = int(p1) if p1.isdigit() else None
            n2 = int(p2) if p2.isdigit() else None

            if n1 is not None and n2 is not None:
                if n1 < n2:
                    return -1
                if n1 > n2:
                    return 1
            elif n1 is not None:
                # Numeric < alphanumeric
                return -1
            elif n2 is not None:
                # Alphanumeric > numeric
                return 1
            else:
                # Both alphanumeric, compare as strings
                if p1 < p2:
                    return -1
                if p1 > p2:
                    return 1

        # All compared parts are equal, check length
        if len(parts1) < len(parts2):
            return -1
        if len(parts1) > len(parts2):
            return 1

        return 0

    # Version bumping methods

    def bump_major(self, *, reset_prerelease: bool = True) -> Version:
        """Bumps the major version and resets minor/patch to 0.

        Args:
            reset_prerelease: If `True`, removes prerelease identifiers.
                Defaults to True.

        Returns:
            A new `Version` instance with incremented major version.

        Examples:
            ```python
            Version("1.2.3").bump_major()
            # Version('v2.0.0')
            Version("1.2.3-beta.1").bump_major()
            # Version('v2.0.0')
            ```
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major + 1,
            minor=0,
            patch=0,
            prerelease="" if reset_prerelease else self.prerelease,
            build=self.build,
        )

    def bump_minor(self, *, reset_prerelease: bool = True) -> Version:
        """Bumps the minor version and resets patch to 0.

        Args:
            reset_prerelease: If `True`, removes prerelease identifiers.
                Defaults to True.

        Returns:
            A new `Version` instance with incremented minor version.

        Examples:
            ```python
            Version("1.2.3").bump_minor()
            # Version('v1.3.0')
            ```
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor + 1,
            patch=0,
            prerelease="" if reset_prerelease else self.prerelease,
            build=self.build,
        )

    def bump_patch(self, *, reset_prerelease: bool = True) -> Version:
        """Bumps the patch version.

        Args:
            reset_prerelease: If `True`, removes prerelease identifiers.
                Defaults to `True`.

        Returns:
            A new `Version` instance with incremented patch version.

        Examples:
            ```python
            Version("1.2.3").bump_patch()
            # Version('v1.2.4')
            ```
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor,
            patch=self.patch + 1,
            prerelease="" if reset_prerelease else self.prerelease,
            build=self.build,
        )

    def bump_prerelease(self) -> Version:
        """Increments the prerelease version number.

        If the current prerelease ends with a number, increments it.
        Otherwise, appends '.1'.

        Returns:
            A new `Version` instance with bumped prerelease.

        Examples:
            ```python
            Version("1.0.0-alpha.1").bump_prerelease()
            # Version('v1.0.0-alpha.2')
            Version("1.0.0-beta").bump_prerelease()
            # Version('v1.0.0-beta.1')
            ```
        """
        if not self.prerelease:
            raise ValueError("Cannot bump prerelease on a stable version")

        parts = self.prerelease.split(".")
        if (s := parts[-1]).isdigit():
            parts[-1] = str(int(s) + 1)  # type: ignore
        else:
            parts.append("1")

        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            prerelease=".".join(parts),
            build=self.build,
        )

    # Prerelease management

    def with_prerelease(self, prerelease: str) -> Version:
        """Returns a new version with the specified prerelease identifier.

        Args:
            prerelease: Prerelease identifier (e.g., `'alpha'`, `'beta.1'`,
                `'rc.2'`).

        Returns:
            A new `Version` instance with the prerelease identifier.

        Examples:
            ```python
            Version("1.0.0").with_prerelease("alpha.1")
            # Version('v1.0.0-alpha.1')
            ```
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            prerelease=prerelease,
            build=self.build,
        )

    def as_alpha(self, number: int | None = None) -> Version:
        """Returns a new version as an alpha prerelease.

        Args:
            number: Optional alpha version number. If `None`, returns `'alpha'`.

        Returns:
            A new `Version` instance with alpha prerelease.

        Examples:
            ```python
            Version("1.0.0").as_alpha()
            # Version('v1.0.0-alpha')
            Version("1.0.0").as_alpha(2)
            # Version('v1.0.0-alpha.2')
            ```
        """
        prerelease = f"alpha.{number}" if number is not None else "alpha"
        return self.with_prerelease(prerelease)

    def as_beta(self, number: int | None = None) -> Version:
        """Returns a new version as a beta prerelease.

        Args:
            number: Optional beta version number. If `None`, returns `'beta'`.

        Returns:
            A new `Version` instance with beta prerelease.

        Examples:
            ```python
            Version("2.0.0").as_beta(1)
            # Version('v2.0.0-beta.1')
            ```
        """
        prerelease = f"beta.{number}" if number is not None else "beta"
        return self.with_prerelease(prerelease)

    def as_rc(self, number: int | None = None) -> Version:
        """Returns a new version as a release candidate.

        Args:
            number: Optional RC version number. If `None`, returns `'rc'`.

        Returns:
            A new `Version` instance with rc prerelease.

        Examples:
            ```python
            Version("3.0.0").as_rc(1)
            # Version('v3.0.0-rc.1')
            ```
        """
        prerelease = f"rc.{number}" if number is not None else "rc"
        return self.with_prerelease(prerelease)

    def as_stable(self) -> Version:
        """Returns a new stable version (removes prerelease and build).

        Returns:
            A new `Version` instance without prerelease or build metadata.

        Examples:
            ```python
            Version("1.0.0-beta.1+build.123").as_stable()
            # Version('v1.0.0')
            ```
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            prerelease="",
            build="",
        )

    # Build metadata

    def with_build(self, build: str) -> Version:
        """Returns a new version with the specified build metadata.

        Args:
            build: Build metadata (e.g., `'build.123'`, `'20230101'`).

        Returns:
            A new `Version` instance with the build metadata.

        Examples:
            ```python
            Version("1.0.0").with_build("build.456")
            # Version('v1.0.0+build.456')
            ```
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            prerelease=self.prerelease,
            build=build,
        )

    def without_build(self) -> Version:
        """Returns a new version without build metadata.

        Returns:
            A new `Version` instance without build metadata.
        """
        return Version(  # pylint: disable=no-value-for-parameter
            major=self.major,
            minor=self.minor,
            patch=self.patch,
            prerelease=self.prerelease,
            build="",
        )

    # Properties and utility methods

    @property
    def is_prerelease(self) -> bool:
        """Checks if this is a prerelease version.

        Returns:
            `True` if prerelease identifier is present, `False` otherwise.
        """
        return bool(self.prerelease)

    @property
    def is_stable(self) -> bool:
        """Checks if this is a stable version.

        Returns:
            `True` if no prerelease identifier is present, `False` otherwise.
        """
        return not self.prerelease

    @property
    def is_alpha(self) -> bool:
        """Checks if this is an alpha prerelease.

        Returns:
            True if prerelease starts with `'alpha'`, False otherwise.
        """
        return self.prerelease.startswith("alpha")

    @property
    def is_beta(self) -> bool:
        """Checks if this is a beta prerelease.

        Returns:
            `True` if prerelease starts with `'beta'`, `False` otherwise.
        """
        return self.prerelease.startswith("beta")

    @property
    def is_rc(self) -> bool:
        """Checks if this is a release candidate.

        Returns:
            True if prerelease starts with `'rc'`, False otherwise.
        """
        return self.prerelease.startswith("rc")

    # String representations
    @property
    def short(self) -> str:
        """Returns short version string (`vMAJOR.MINOR`).

        Returns:
            Short version string.

        Examples:
            ```python
            Version("1.2.3").short
            # 'v1.2'
            Version("1.2.3-alpha.1").short
            # 'v1.2-alpha.1'
            ```
        """
        result = f"v{self.major}.{self.minor}"
        if self.prerelease:
            result += f"-{self.prerelease}"
        return result

    @property
    def major_only(self) -> str:
        """Returns major-only version string (`vMAJOR`).

        Returns:
            Major-only version string.

        Examples:
            ```python
            Version("1.2.3").major_only
            # 'v1'
            ```
        """
        return f"v{self.major}"

    def to_string(
        self,
        *,
        include_v: bool = True,
        include_prerelease: bool = True,
        include_build: bool = True,
    ) -> str:
        """Returns a customizable string representation.

        Args:
            include_v: Include `'v'` prefix. Defaults to `True`.
            include_prerelease: Include prerelease identifier. Defaults to `True`.
            include_build: Include build metadata. Defaults to `True`.

        Returns:
            Customized version string.

        Examples:
            ```python
            v = Version("1.2.3-beta.1+build.123")
            v.to_string(include_v=False)
            # '1.2.3-beta.1+build.123'
            v.to_string(include_prerelease=False, include_build=False)
            # 'v1.2.3'
            ```
        """
        result = f"{self.major}.{self.minor}.{self.patch}"

        if include_prerelease and self.prerelease:
            result += f"-{self.prerelease}"

        if include_build and self.build:
            result += f"+{self.build}"

        if include_v:
            result = f"v{result}"

        return result

    @property
    def tuple(self) -> builtins.tuple[int, int, int]:
        """Returns version as a tuple `(major, minor, patch)`.

        Returns:
            Tuple of `(major, minor, patch)`.

        Examples:
            ```python
            Version("1.2.3").tuple
            # (1, 2, 3)
            ```
        """
        return self.major, self.minor, self.patch

    @property
    def full_tuple(self) -> builtins.tuple[int, int, int, str, str]:
        """Returns full version as a tuple including prerelease and build.

        Returns:
            Tuple of (major, minor, patch, prerelease, build).
        """
        return self.major, self.minor, self.patch, self.prerelease, self.build
