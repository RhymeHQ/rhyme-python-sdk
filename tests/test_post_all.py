from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING

import httpx2
import pytest
from conftest import API_KEY, BASE_URL, batch_body, respond

from rhyme import (
    ActivityBatchResult,
    BadRequestError,
    InternalServerError,
    PayloadTooLargeError,
    PostAllError,
    Rhyme,
)
from rhyme.testing import AsyncFakeRhyme, FakeRhyme

if TYPE_CHECKING:
    from rhyme._base_client import ActivityInput


def _item(i: int) -> dict:
    return {"externalId": f"a{i}", "status": "done", "action": "reply"}


def _ids(items: Iterable[ActivityInput]) -> list[str]:
    return [dict(a)["externalId"] for a in items]


def test_chunks_into_batches_in_order():
    fake = FakeRhyme()
    result = fake.post_all(_item(i) for i in range(250))
    assert [len(b) for b in fake.batches] == [100, 100, 50]
    assert _ids(fake.posted) == [f"a{i}" for i in range(250)]
    assert [b.counts.created for b in result.batch_results] == [100, 100, 50]
    assert result.dropped == []


def test_max_items():
    fake = FakeRhyme()
    fake.post_all([_item(i) for i in range(5)], max_items=2)
    assert [len(b) for b in fake.batches] == [2, 2, 1]
    for bad in (0, 101):
        with pytest.raises(ValueError):
            fake.post_all([_item(1)], max_items=bad)


def test_empty_input_posts_nothing():
    fake = FakeRhyme()
    result = fake.post_all([])
    assert fake.batches == [] and result.batch_results == [] and result.dropped == []


class _LimitedFake(FakeRhyme):
    """Refuses batches over `limit` items with a 413, and any batch holding `poison` with a 400."""

    def __init__(self, limit: int, poison: str | None = None):
        super().__init__()
        self.limit = limit
        self.poison = poison

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        batch = list(activities)
        if len(batch) > self.limit:
            raise PayloadTooLargeError("too large", status_code=413, code="payload_too_large")
        if self.poison in _ids(batch):
            raise BadRequestError("bad", status_code=400, code="invalid_request")
        return super().post_activities(batch)


def test_too_large_batch_is_split():
    fake = _LimitedFake(limit=3)
    result = fake.post_all([_item(i) for i in range(10)])
    assert max(len(b) for b in fake.batches) <= 3
    assert _ids(fake.posted) == [f"a{i}" for i in range(10)]
    assert sum(b.counts.created for b in result.batch_results) == 10 and result.dropped == []


def test_unacceptable_item_is_dropped_and_rest_posted():
    fake = _LimitedFake(limit=100, poison="a2")
    result = fake.post_all([_item(i) for i in range(5)])
    assert _ids(fake.posted) == ["a0", "a1", "a3", "a4"]
    assert [_ids([d.activity]) for d in result.dropped] == [["a2"]]
    assert isinstance(result.dropped[0].error, BadRequestError)


def test_unserialisable_item_is_dropped():
    def reply(request: httpx2.Request) -> httpx2.Response:
        items = json.loads(request.content)["activities"]
        return respond(200, batch_body(*["created"] * len(items)))

    client = Rhyme(api_key=API_KEY, base_url=BASE_URL, http_client=httpx2.Client(transport=httpx2.MockTransport(reply)))
    result = client.post_all([_item(1), {**_item(2), "metadata": {"at": object()}}])
    assert len(result.dropped) == 1 and isinstance(result.dropped[0].error, TypeError)
    assert sum(b.counts.created for b in result.batch_results) == 1


class _FailsOnCall(FakeRhyme):
    """Fails the `n`th post (from 1) with a 503."""

    def __init__(self, n: int):
        super().__init__()
        self.n = n
        self.calls = 0

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        self.calls += 1
        if self.calls == self.n:
            raise InternalServerError("down", status_code=503)
        return super().post_activities(activities)


def test_other_errors_stop_with_unsent_items():
    fake = _FailsOnCall(n=2)
    with pytest.raises(PostAllError) as exc:
        fake.post_all([_item(i) for i in range(5)], max_items=2)
    assert isinstance(exc.value.__cause__, InternalServerError)
    assert len(exc.value.batch_results) == 1 and exc.value.dropped == []
    assert _ids(exc.value.unsent) == ["a2", "a3", "a4"]
    assert fake.calls == 2


def test_unsent_includes_queued_halves_and_keeps_dropped():
    class Fake(_LimitedFake):
        def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
            batch = list(activities)
            if _ids(batch) == ["a2"]:
                raise InternalServerError("down", status_code=503)
            return super().post_activities(batch)

    # a0..a5 → 413 → [a0 a1 a2] → 413 → a0 posted; [a1 a2] → 400 → a1 dropped; a2 → 503, with [a3 a4 a5] queued.
    with pytest.raises(PostAllError) as exc:
        Fake(limit=2, poison="a1").post_all([_item(i) for i in range(6)])
    assert len(exc.value.batch_results) == 1
    assert _ids(d.activity for d in exc.value.dropped) == ["a1"]
    assert _ids(exc.value.unsent) == ["a2", "a3", "a4", "a5"]


def test_async_post_all():
    async def run() -> None:
        fake = AsyncFakeRhyme()
        result = await fake.post_all([_item(i) for i in range(5)], max_items=2)
        assert [len(b) for b in fake.batches] == [2, 2, 1]
        assert sum(b.counts.created for b in result.batch_results) == 5

        fake.error = InternalServerError("down", status_code=503)
        with pytest.raises(PostAllError) as exc:
            await fake.post_all([_item(i) for i in range(3)], max_items=2)
        assert _ids(exc.value.unsent) == ["a0", "a1", "a2"]

        fake.error = PayloadTooLargeError("too large", status_code=413)
        result = await fake.post_all([_item(i) for i in range(4)])
        assert [len(b.results) for b in result.batch_results] == [2, 2] and result.dropped == []

    asyncio.run(run())
