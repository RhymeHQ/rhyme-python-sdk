from __future__ import annotations

import pytest

from rhyme import (
    Activity,
    ActivityBatchResult,
    ActivityRequest,
    ActivityResult,
    BatchCounts,
    InternalServerError,
    ItemError,
)
from rhyme.testing import FakeRhyme


def test_fake_records_payloads_as_the_client_sends_them():
    fake = FakeRhyme()
    fake.post_activity(
        Activity(external_id="t1", status="done", action="reply", request=ActivityRequest(source="zd", id="1"))
    )
    fake.post_activities(
        [{"externalId": "t2", "status": "pending", "anything": 1}, Activity(external_id="t3", status="done")]
    )

    assert fake.batches == [
        [{"externalId": "t1", "status": "done", "action": "reply", "request": {"source": "zd", "id": "1"}}],
        [{"externalId": "t2", "status": "pending", "anything": 1}, {"externalId": "t3", "status": "done"}],
    ]
    assert [item["externalId"] for item in fake.posted] == ["t1", "t2", "t3"]


def test_fake_returns_created_for_every_item_by_default():
    result = FakeRhyme().post_activities([Activity(external_id=f"t{i}", status="done") for i in range(3)])
    assert [r.result for r in result.results] == ["created"] * 3
    assert [r.external_id for r in result.results] == ["t0", "t1", "t2"]
    assert result.counts.created == 3 and result.rejected == []
    assert all(r.resolved is not None for r in result.results)


def test_fake_returns_a_scripted_result_once():
    fake = FakeRhyme()
    scripted = ActivityBatchResult(
        request_id="req_x",
        counts=BatchCounts(received=1, created=0, updated=0, stale=0, rejected=1),
        results=[ActivityResult(index=0, external_id="t1", result="rejected", error=ItemError(code="invalid_field"))],
    )
    fake.result = scripted
    assert fake.post_activities([Activity(external_id="t1", status="done")]) is scripted
    assert fake.post_activity(Activity(external_id="t1", status="done")).result == "created"
    assert len(fake.batches) == 2


def test_fake_raises_a_scripted_error_once_without_recording():
    fake = FakeRhyme()
    fake.error = InternalServerError("boom", status_code=500)
    with pytest.raises(InternalServerError):
        fake.post_activity(Activity(external_id="t1", status="done"))
    assert fake.batches == []
    fake.post_activity(Activity(external_id="t1", status="done"))
    assert len(fake.batches) == 1


def test_fake_checks_batch_size_like_the_client():
    with pytest.raises(ValueError):
        FakeRhyme().post_activities([])


def test_fake_me():
    fake = FakeRhyme()
    assert "activities:write" in fake.me().key.scopes
    assert fake.me_calls == 1
