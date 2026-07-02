from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    _U = t.TypeVar("_U")
    _V = t.TypeVar("_V")


class AsyncStream[T](t.AsyncIterable[T]):
    r"""Async stream with functional operations for processing sequences.

    Provides a fluent interface for transforming, filtering, and consuming
    asynchronous data streams. Supports caching of consumed items for
    replay and introspection.

    The stream can be iterated multiple times - first iteration consumes
    from the source and caches items, subsequent iterations replay from cache.

    Example:
        ```python
        # Create from async generator
        async def number_gen():
            for i in range(10):
                yield i

        stream = AsyncStream(number_gen())


        # Functional operations
        even_numbers = stream.filter(lambda x: x % 2 == 0)
        doubled = even_numbers.map(lambda x: x * 2)
        first_three = doubled.take(3)

        async for num in first_three:
            print(num)  # 0, 4, 8


        # Stream completion chunks
        completion_stream = await system.complete(messages, stream=True)

        # Extract only content
        content = completion_stream \\
            .filter(lambda c: c.delta.content is not None) \\
            .map(lambda c: c.delta.content)

        # Print until finish
        await content.foreach(lambda text: print(text, end=""))


        # Replay capability
        stream = AsyncStream(gen())
        async for item in stream:
            print(item)  # First iteration: consumes source

        async for item in stream:
            print(item)  # Second iteration: replays from cache
        ```

    Note:
        - First iteration consumes the source and caches all items
        - Subsequent iterations replay cached items without re-consuming source
        - Transformation operations create new streams (lazy evaluation)
        - Exceptions during consumption are cached and re-raised on replay
    """

    def __init__(self, source: t.AsyncIterable[_V], mapper: t.Callable[[_V], T] | None = None):
        self._source = source
        self._mapper = mapper
        self._is_consumed = False
        self._items = []  # type: t.List[T]
        self._error = None  # type: t.Optional[Exception]
        self._completed = False
        self._iterator = None  # type: t.Optional[t.AsyncIterator[T]]

    async def __aiter__(self) -> t.AsyncIterator[T]:  # pylint: disable=invalid-overridden-method
        """Iterate over stream items, caching on first pass."""
        if self._is_consumed:
            # If the stream has already been consumed, return the cached items
            for item in self._items:
                yield item
            return

        self._is_consumed = True
        try:
            async for raw_item in self._source:
                item = self._mapper(raw_item) if self._mapper else raw_item  # type: ignore[assignment]

                self._items.append(item)
                yield item

        except Exception as e:
            self._error = e
            raise
        finally:
            self._completed = True

    async def __anext__(self) -> T:
        """Get next item (supports direct iterator protocol)."""
        if not hasattr(self, "_iterator") or self._iterator is None:
            self._iterator = self.__aiter__()
        return await self._iterator.__anext__()

    def filter(self, predicate: t.Callable[[T], bool]) -> AsyncStream[T]:
        """Filter stream items by predicate.

        Args:
            predicate: Function returning True for items to keep.

        Returns:
            New stream containing only items matching predicate.
        """

        async def filtered_source() -> t.AsyncIterator[T]:
            async for item in self:
                if predicate(item):
                    yield item

        return AsyncStream(filtered_source())

    def tap(self, action: t.Callable[[T], None]) -> AsyncStream[T]:
        """Perform side effect on each item without transforming.

        Args:
            action: Function to call for each item.

        Returns:
            New stream with same items, action called on each.
        """

        async def tap_source() -> t.AsyncIterator[T]:
            async for item in self:
                action(item)
                yield item

        return AsyncStream(tap_source())

    def map(self, mapper: t.Callable[[T], _U]) -> AsyncStream[_U]:
        """Transform each stream item.

        Args:
            mapper: Function to transform each item.

        Returns:
            New stream with transformed items.
        """

        async def mapped_source() -> t.AsyncIterator[_U]:
            async for item in self:
                yield mapper(item)

        return AsyncStream(mapped_source())

    def take(self, n: int, /) -> AsyncStream[T]:
        """Take only first n items.

        Args:
            n: Number of items to take.

        Returns:
            New stream with at most n items.
        """

        async def take_source() -> t.AsyncIterator[T]:
            count = 0
            async for item in self:
                if count >= n:
                    break
                yield item
                count += 1

        return AsyncStream(take_source())

    def skip(self, n: int, /) -> AsyncStream[T]:
        """Skip first n items.

        Args:
            n: Number of items to skip.

        Returns:
            New stream starting after n items.
        """

        async def skip_source() -> t.AsyncIterator[T]:
            count = 0
            async for item in self:
                if count < n:
                    count += 1
                    continue
                yield item

        return AsyncStream(skip_source())

    def chunk(self, size: int) -> AsyncStream[list[T]]:
        """Group items into batches.

        Args:
            size: Maximum size of each batch.

        Returns:
            New stream emitting lists of items.
        """

        async def chunk_source() -> t.AsyncIterator[list[T]]:
            batch = []
            async for item in self:
                batch.append(item)
                if len(batch) >= size:
                    yield batch
                    batch = []
            if batch:
                yield batch

        return AsyncStream(chunk_source())

    def take_while(self, predicate: t.Callable[[T], bool]) -> AsyncStream[T]:
        """Take items while predicate is true.

        Args:
            predicate: Function tested for each item.

        Returns:
            New stream ending when predicate first returns False.
        """

        async def take_while_source() -> t.AsyncIterator[T]:
            async for item in self:
                if not predicate(item):
                    break
                yield item

        return AsyncStream(take_while_source())

    def enumerate(self, start: int = 0) -> AsyncStream[tuple[int, T]]:
        """Add index to each item.

        Args:
            start: Starting index value.

        Returns:
            New stream emitting (index, item) tuples.
        """

        async def enumerate_source() -> t.AsyncIterator[tuple[int, T]]:
            index = start
            async for item in self:
                yield index, item
                index += 1

        return AsyncStream(enumerate_source())

    async def foreach(self, action: t.Callable[[T], t.Awaitable[None]], /) -> None:
        """Consume stream, performing async action on each item.

        Args:
            action: Async function to call for each item.
        """
        async for item in self:
            await action(item)

    async def reduce(self, func: t.Callable[[_U, T], _U], initial: _U) -> _U:
        """Reduce stream to single value.

        Args:
            func: Reducer function (accumulator, item) -> new_accumulator.
            initial: Initial accumulator value.

        Returns:
            Final accumulated value.
        """
        result = initial
        async for item in self:
            result = func(result, item)
        return result

    async def all(self, predicate: t.Callable[[T], bool] | None = None, /) -> bool:
        """Test if all items match predicate.

        Args:
            predicate: Test function. If None, tests truthiness.

        Returns:
            True if all items match, False otherwise.
        """
        if predicate is None:

            def predicate(v: T) -> bool:
                return bool(v)

        async for item in self:
            if not predicate(item):
                return False
        return True

    @property
    def is_completed(self) -> bool:
        """Check if stream has been fully consumed."""
        return self._completed

    @property
    def error(self) -> Exception | None:
        """Get exception if one occurred during consumption."""
        return self._error

    @property
    def items_count(self) -> int:
        """Get number of items consumed so far."""
        return len(self._items)
