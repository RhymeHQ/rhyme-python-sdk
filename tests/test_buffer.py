from __future__ import annotations

import json
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import httpx2
import pytest
from conftest import API_KEY, BASE_URL, batch_body, respond

from rhyme import (
    ActivityBatchResult,
    ActivityBuffer,
    BadRequestError,
    FlushError,
    InternalServerError,
    PayloadTooLargeError,
    Rhyme,
)
from rhyme.testing import FakeRhyme

if TYPE_CHECKING:
    from rhyme._base_client import ActivityInput


def _item(i: int) -> dict:
    return {"externalId": f"a{i}", "status": "done", "action": "reply"}


def test_add_posts_full_batches():
    fake = FakeRhyme()
    buf = ActivityBuffer(fake, max_items=2)
    assert buf.add(_item(1)) == []
    results = buf.add(_item(2))
    assert len(results) == 1 and results[0].counts.created == 2
    assert len(buf) == 0


def test_context_manager_flushes_remainder():
    fake = FakeRhyme()
    with ActivityBuffer(fake, max_items=2) as buf:
        for i in range(5):
            buf.add(_item(i))
    assert len(fake.posted) == 5


def test_failed_post_keeps_items():
    fake = FakeRhyme()
    buf = ActivityBuffer(fake, max_items=10)
    buf.add(_item(1))
    fake.error = InternalServerError("boom", status_code=500)
    with pytest.raises(FlushError) as exc:
        buf.flush()
    assert isinstance(exc.value.__cause__, InternalServerError)
    assert exc.value.dropped == [] and exc.value.results == []
    assert len(buf) == 1
    buf.flush()
    assert len(buf) == 0 and len(fake.posted) == 1


class _SlowFake(FakeRhyme):
    """Takes a moment to post, as a real network call would, so other threads run mid-post."""

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        batch = list(activities)
        time.sleep(0.001)
        return super().post_activities(batch)


def test_threaded_adds_post_each_item_once():
    fake = _SlowFake()
    buf = ActivityBuffer(fake, max_items=5)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: buf.add(_item(i)), range(200)))
    buf.flush()
    assert sorted(item["externalId"] for item in fake.posted) == sorted(f"a{i}" for i in range(200))


def test_max_items_bounds():
    with pytest.raises(ValueError):
        ActivityBuffer(FakeRhyme(), max_items=101)


class _LimitedFake(FakeRhyme):
    """Refuses batches over `limit` items with a 413, and the item `poison` on its own with a 400."""

    def __init__(self, limit: int, poison: str | None = None):
        super().__init__()
        self.limit = limit
        self.poison = poison
        self.batch_sizes: list[int] = []

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        batch = list(activities)
        if len(batch) > self.limit:
            raise PayloadTooLargeError("too large", status_code=413, code="payload_too_large")
        if any(dict(a)["externalId"] == self.poison for a in batch):
            raise BadRequestError("bad", status_code=400, code="invalid_request")
        self.batch_sizes.append(len(batch))
        return super().post_activities(batch)


def test_too_large_batch_is_split():
    fake = _LimitedFake(limit=3)
    buf = ActivityBuffer(fake, max_items=10)
    for i in range(10):
        buf.add(_item(i))
    assert len(fake.posted) == 10 and max(fake.batch_sizes) <= 3
    assert [item["externalId"] for item in fake.posted] == [f"a{i}" for i in range(10)]
    assert len(buf) == 0


def test_unacceptable_item_is_dropped_and_rest_kept():
    fake = _LimitedFake(limit=100, poison="a2")
    buf = ActivityBuffer(fake, max_items=10)
    for i in range(5):
        buf.add(_item(i))
    with pytest.raises(FlushError) as exc:
        buf.flush()
    assert isinstance(exc.value.__cause__, BadRequestError)
    assert [dict(a)["externalId"] for a in exc.value.dropped] == ["a2"]
    # a0 and a1 were posted before the bad item was isolated; their results aren't lost.
    assert sum(r.counts.created for r in exc.value.results) == 2
    assert len(buf) == 2
    buf.flush()
    assert sorted(item["externalId"] for item in fake.posted) == ["a0", "a1", "a3", "a4"]


def test_unserialisable_item_is_dropped():
    def reply(request: httpx2.Request) -> httpx2.Response:
        items = json.loads(request.content)["activities"]
        return respond(200, batch_body(*["created"] * len(items)))

    client = Rhyme(api_key=API_KEY, base_url=BASE_URL, http_client=httpx2.Client(transport=httpx2.MockTransport(reply)))
    buf = ActivityBuffer(client, max_items=10)
    buf.add(_item(1))
    buf.add({**_item(2), "metadata": {"at": object()}})
    with pytest.raises(FlushError) as exc:
        buf.flush()
    assert isinstance(exc.value.__cause__, TypeError)
    assert len(exc.value.dropped) == 1 and len(buf) == 0
    assert sum(r.counts.created for r in exc.value.results) == 1
