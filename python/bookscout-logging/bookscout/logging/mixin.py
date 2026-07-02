from __future__ import annotations

import inspect
import typing as t

from bookscout.core import __app__

from . import Logger


class LoggingMixin:
    """Mixin class for providing logging functionality with automatic tag
    generation.

    This mixin automatically generates a log tag based on the class's module
    name and provides a logger instance with that tag. The tag can be
    overridden by setting the `__logtag__` class variable.

    Attributes:
        __logtag__: Class variable for the logging tag. Auto-generated if not
            set.
        logger: Logger instance tagged with `__logtag__`.
    """

    __logtag__: t.ClassVar[str]

    def __init_subclass__(cls, *args: t.Any, **kwargs: t.Any) -> None:
        """Hook for subclass initialisation to auto-generate log tags.

        This method automatically generates a log tag from the module name if
        one is not explicitly set. Abstract classes are skipped.

        Args:
            *args: Variable length argument list.
            **kwargs: Arbitrary keyword arguments passed to
                super().__init_subclass__.
        """
        super().__init_subclass__(*args, **kwargs)

        if inspect.isabstract(cls):
            return

        if hasattr(cls, "__logtag__") and cls.__logtag__ is not None:
            return

        module = cls.__module__

        # strip prefix
        if module.startswith(__app__ + "."):
            module = module[len(__app__) + 1 :]

        parts = module.split(".")
        if len(parts) == 1:
            # fallback
            layer = parts[0]
            last = parts[0]
        else:
            layer = ".".join(parts[:-1])
            last = parts[-1]

        cls.__logtag__ = f"{layer}.{last.upper()}"

    def __init__(self, *args: t.Any, logger: Logger, **kwargs: t.Any):
        self.logger = logger.with_context(tag=self.__logtag__)
        super().__init__(*args, **kwargs)
