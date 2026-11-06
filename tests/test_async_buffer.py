from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import TYPE_CHECKING

import pytest

from rhyme import ActivityBatchResult, AsyncActivityBuffer, FlushError, InternalServerError, PayloadTooLargeError
from rhyme.testing import AsyncFakeRhyme

if TYPE_CHECKING:
    from rhyme._base_client import ActivityInput


def _item(i: int) -> dict:
    return {"externalId": f"a{i}", "status": "done", "action": "reply"}


def test_add_posts_full_batches():
    async def run() -> None:
        fake = AsyncFakeRhyme()
        buf = AsyncActivityBuffer(fake, max_items=2)
        assert await buf.add(_item(1)) == []
        results = await buf.add(_item(2))
        assert len(results) == 1 and results[0].counts.created == 2
        assert len(buf) == 0

    asyncio.run(run())


def test_context_manager_flushes_remainder():
    async def run() -> None:
        fake = AsyncFakeRhyme()
        async with AsyncActivityBuffer(fake, max_items=2) as buf:
            for i in range(5):
                await buf.add(_item(i))
        assert len(fake.posted) == 5

    asyncio.run(run())


def test_failed_post_keeps_items():
    async def run() -> None:
        fake = AsyncFakeRhyme()
        buf = AsyncActivityBuffer(fake, max_items=10)
        await buf.add(_item(1))
        fake.error = InternalServerError("boom", status_code=500)
        with pytest.raises(FlushError) as exc:
            await buf.flush()
        assert isinstance(exc.value.__cause__, InternalServerError)
        assert len(buf) == 1
        await buf.flush()
        assert len(buf) == 0 and len(fake.posted) == 1

    asyncio.run(run())


class _YieldingFake(AsyncFakeRhyme):
    """Yields to the event loop mid-post, as a real network call would."""

    async def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        batch = list(activities)
        await asyncio.sleep(0)
        return await super().post_activities(batch)


def test_concurrent_adds_post_each_item_once():
    async def run() -> None:
        fake = _YieldingFake()
        buf = AsyncActivityBuffer(fake, max_items=2)
        await asyncio.gather(*(buf.add(_item(i)) for i in range(9)))
        await buf.flush()
        assert sorted(item["externalId"] for item in fake.posted) == sorted(f"a{i}" for i in range(9))

    asyncio.run(run())


def test_max_items_bounds():
    with pytest.raises(ValueError):
        AsyncActivityBuffer(AsyncFakeRhyme(), max_items=0)


class _LimitedFake(AsyncFakeRhyme):
    """Refuses batches over `limit` items with a 413."""

    def __init__(self, limit: int):
        super().__init__()
        self.limit = limit

    async def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        batch = list(activities)
        if len(batch) > self.limit:
            raise PayloadTooLargeError("too large", status_code=413, code="payload_too_large")
        return await super().post_activities(batch)


def test_too_large_batch_is_split_and_lone_item_dropped():
    async def run() -> None:
        fake = _LimitedFake(limit=2)
        buf = AsyncActivityBuffer(fake, max_items=5)
        for i in range(5):
            await buf.add(_item(i))
        assert [item["externalId"] for item in fake.posted] == [f"a{i}" for i in range(5)]

        fake.limit = 0  # now even a single item is too large
        await buf.add(_item(5))
        with pytest.raises(FlushError) as exc:
            await buf.flush()
        assert len(exc.value.dropped) == 1 and len(buf) == 0

    asyncio.run(run())
