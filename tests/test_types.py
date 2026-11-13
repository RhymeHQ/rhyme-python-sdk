from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from rhyme import (
    Activity,
    ActivityMode,
    ActivityRequest,
    ActivityResult,
    ActivityStatus,
    ModelUsage,
    Reference,
    StepRef,
    ToolCall,
    WorkflowRef,
)
from rhyme.types import INT32_MAX


def test_workflow_one_of():
    assert WorkflowRef(key="runbook-X", version=2).to_api() == {"key": "runbook-X", "version": 2}
    assert WorkflowRef(id="5f0c").to_api() == {"id": "5f0c"}
    with pytest.raises(ValidationError):
        WorkflowRef()
    with pytest.raises(ValidationError):
        WorkflowRef(key="a", id="b")
    with pytest.raises(ValidationError):
        WorkflowRef(id="b", version=1)
    with pytest.raises(ValidationError):
        StepRef()


def test_cached_tokens_within_input():
    with pytest.raises(ValidationError):
        ModelUsage(model="m", input_tokens=10, output_tokens=1, cached_input_tokens=11)


def test_occurred_at_needs_offset_and_serializes_rfc3339():
    with pytest.raises(ValidationError):
        Activity(external_id="a", status="done", occurred_at=datetime(2026, 11, 5, 9, 0))
    a = Activity(external_id="a", status="done", occurred_at=datetime(2026, 11, 5, 9, 0, tzinfo=UTC))
    assert a.to_api()["occurredAt"] == "2026-11-05T09:00:00Z"
    parsed = Activity.model_validate({"externalId": "a", "status": "done", "occurredAt": "2026-11-05T10:00:00+01:00"})
    assert parsed.to_api()["occurredAt"] == "2026-11-05T10:00:00+01:00"


def test_activity_rejects_unknown_fields_and_bad_ids():
    with pytest.raises(ValidationError):
        Activity(external_id="a", status="done", sumary="typo")  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        Activity(external_id="has space", status="done")
    with pytest.raises(ValidationError):
        Activity(external_id="a", status="done", action="DraftReply")


def test_shadow_mode():
    assert Activity(external_id="a", status="done", mode=ActivityMode.SHADOW).to_api()["mode"] == "shadow"
    assert Activity(external_id="a", status="done", mode="live").mode == ActivityMode.LIVE
    assert "mode" not in Activity(external_id="a", status="done").to_api()
    with pytest.raises(ValidationError):
        Activity.model_validate({"externalId": "a", "status": "done", "mode": "replay"})
    with pytest.raises(ValidationError):
        Activity(external_id="a", status="done", mode=None)


def test_no_action_status():
    assert Activity(external_id="a", status=ActivityStatus.NO_ACTION).to_api()["status"] == "no_action"
    assert Activity.model_validate({"externalId": "a", "status": "no_action"}).status == ActivityStatus.NO_ACTION


def test_activity_accepts_camel_case_input():
    a = Activity.model_validate({"externalId": "a", "status": "pending", "escalationReason": "x"})
    assert a.escalation_reason == "x"


def test_response_tolerates_new_fields():
    r = ActivityResult.model_validate({"index": 0, "result": "created", "somethingNew": 1})
    assert r.ok


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (Activity, {"external_id": "a", "status": "done", "action": None}),
        (Activity, {"external_id": "a", "status": "done", "occurred_at": None}),
        (Activity, {"external_id": "a", "status": "done", "request": None}),
        (Activity, {"external_id": "a", "status": "done", "workflow": None}),
        (Activity, {"external_id": "a", "status": "done", "step": None}),
        (Activity, {"external_id": "a", "status": "done", "intent": None}),
        (Activity, {"external_id": "a", "status": "done", "references": None}),
        (Activity, {"external_id": "a", "status": "done", "metadata": None}),
        (ActivityRequest, {"source": "zendesk", "id": "1", "url": None}),
        (WorkflowRef, {"key": "k", "version": None}),
        (StepRef, {"key": "k", "id": None}),
        (ModelUsage, {"model": "m", "input_tokens": 1, "output_tokens": 1, "cached_input_tokens": None}),
        (ToolCall, {"name": "t", "ok": None}),
        (ToolCall, {"name": "t", "duration_ms": None}),
    ],
)
def test_explicit_null_rejected_where_api_forbids_it(model, kwargs):
    with pytest.raises(ValidationError, match="cannot be null"):
        model(**kwargs)


def test_explicit_null_clears_nullable_fields():
    a = Activity(
        external_id="a",
        status="done",
        summary=None,
        output=None,
        duration_ms=None,
        usage=None,
        tools=None,
        references=[Reference(system="s", ref="r", url=None)],
    )
    assert a.to_api() == {
        "externalId": "a",
        "status": "done",
        "summary": None,
        "output": None,
        "durationMs": None,
        "usage": None,
        "tools": None,
        "references": [{"system": "s", "ref": "r", "url": None}],
    }


def test_integers_capped_at_int32():
    Activity(external_id="a", status="done", duration_ms=INT32_MAX)
    with pytest.raises(ValidationError):
        Activity(external_id="a", status="done", duration_ms=INT32_MAX + 1)
    with pytest.raises(ValidationError):
        ModelUsage(model="m", input_tokens=INT32_MAX + 1, output_tokens=0)
