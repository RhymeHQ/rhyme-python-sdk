from __future__ import annotations

from rhyme import Activity, ActivityRequest
from rhyme.testing import FakeRhyme


def test_fake_applies_upsert_rules():
    fake = FakeRhyme()
    created = fake.post_activity(Activity(external_id="t1", status="pending", action="draft_reply"))
    updated = fake.post_activity(Activity(external_id="t1", status="done"))
    stale = fake.post_activity(Activity(external_id="t1", status="failed"))
    unknown = fake.post_activity(Activity(external_id="t2", status="done"))

    assert [r.result for r in (created, updated, stale, unknown)] == ["created", "updated", "stale", "rejected"]
    assert created.activity_id == updated.activity_id
    assert unknown.error is not None and unknown.error.code == "unknown_activity"
    assert fake.activities["t1"]["status"] == "done"
    assert len(fake.posted) == 4


def test_fake_rejects_invalid_items_per_item():
    fake = FakeRhyme()
    result = fake.post_activities(
        [{"externalId": "ok", "status": "done", "action": "reply"}, {"externalId": "bad id", "status": "done"}]
    )
    assert result.counts.created == 1 and result.counts.rejected == 1
    error = result.rejected[0].error
    assert error is not None
    assert (error.code, error.field) == ("invalid_field", "externalId")


def test_fake_me():
    assert "activities:write" in FakeRhyme().me().key.scopes


def test_fake_keeps_set_once_fields():
    fake = FakeRhyme()
    fake.post_activity(
        Activity(external_id="t1", status="pending", action="reply", request=ActivityRequest(source="zd", id="1"))
    )
    same = fake.post_activity(
        Activity(external_id="t1", status="pending", request=ActivityRequest(source="zd", id="1"))
    )
    other = fake.post_activity(Activity(external_id="t1", status="done", request=ActivityRequest(source="zd", id="2")))
    assert same.warnings == []
    assert [(w.code, w.field) for w in other.warnings] == [("immutable_field_ignored", "request")]
    assert fake.activities["t1"]["request"] == {"source": "zd", "id": "1"}
    assert fake.activities["t1"]["status"] == "done"


def test_fake_merges_metadata_and_cleans_text():
    fake = FakeRhyme()
    fake.post_activity(Activity(external_id="t1", status="pending", action="reply", metadata={"a": 1, "b": 2}))
    fake.post_activity(Activity(external_id="t1", status="done", metadata={"b": 3}, summary="  hi  ", ask="   "))
    stored = fake.activities["t1"]
    assert stored["metadata"] == {"a": 1, "b": 3}
    assert stored["summary"] == "hi" and stored["ask"] is None


def test_fake_rejects_explicit_null_on_non_nullable_field():
    result = FakeRhyme().post_activity({"externalId": "t1", "status": "done", "action": "reply", "intent": None})
    assert result.error is not None and (result.error.code, result.error.field) == ("invalid_field", "intent")
