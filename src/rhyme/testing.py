"""In-memory stand-ins for `rhyme.Rhyme` and `rhyme.AsyncRhyme`, for testing code that reports activities.

The fakes apply the API's upsert rules: create / update / stale / reject, set-once fields, metadata merging and text
trimming. They don't model truncation, PII redaction, workflow/intent linking, rate limits or quotas.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ._base_client import ActivityInput, activity_payloads
from ._exceptions import RhymeError
from .types import (
    FINAL_STATUSES,
    MAX_BATCH,
    Activity,
    ActivityBatchResult,
    ActivityResult,
    BatchCounts,
    ItemError,
    ItemWarning,
    Me,
    MeAgent,
    MeKey,
    MeLimits,
    MeUsage,
)

# Kept from the first post that sets them; a later, different value is ignored with a warning.
_SET_ONCE = ("request", "occurredAt", "workflow", "step", "intent")
_TEXT = ("requester", "ask", "summary", "outcome", "escalationReason", "output")


def _clean(item: dict[str, Any]) -> dict[str, Any]:
    """Trim text fields and store blank text as None, as the API does."""
    cleaned = dict(item)
    for key in _TEXT:
        if isinstance(cleaned.get(key), str):
            cleaned[key] = cleaned[key].strip() or None
    return cleaned


class _FakeStore:
    """Records what was posted and applies the API's upsert rules (create, update, stale, reject)."""

    def __init__(self, *, me: Me | None = None) -> None:
        self.posted: list[dict[str, Any]] = []
        self.activities: dict[str, dict[str, Any]] = {}
        self.error: RhymeError | None = None
        self._me_result = me or Me(
            agent=MeAgent(id=str(uuid.uuid4()), name="Test agent"),
            org_id=str(uuid.uuid4()),
            key=MeKey(prefix="rhyme_ak1_00000000", name="test", scopes=["activities:write"]),
            limits=MeLimits(
                max_items=MAX_BATCH,
                max_body_bytes=1_048_576,
                requests_per_minute=120,
                items_per_minute=1000,
                daily_items=20000,
            ),
            usage=MeUsage(day=datetime.now(UTC).date(), items=0),
            server_time=datetime.now(UTC),
        )
        self._request_ids = itertools.count(1)

    def _me(self) -> Me:
        self._raise_pending_error()
        return self._me_result

    def _post(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        self._raise_pending_error()
        items = activity_payloads(activities)
        self.posted.extend(items)
        results = [self._upsert(i, item) for i, item in enumerate(items)]
        counts = {k: sum(r.result == k for r in results) for k in ("created", "updated", "stale", "rejected")}
        return ActivityBatchResult(
            request_id=f"req_fake_{next(self._request_ids)}",
            counts=BatchCounts(received=len(items), **counts),
            results=results,
        )

    def _raise_pending_error(self) -> None:
        if self.error is not None:
            err, self.error = self.error, None
            raise err

    def _upsert(self, index: int, item: dict[str, Any]) -> ActivityResult:
        ext = item.get("externalId")
        echo = ext if isinstance(ext, str) and len(ext) <= 200 else None
        try:
            ext = Activity.model_validate(item, by_name=False).external_id
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(p) for p in first["loc"])
            return _rejected(index, echo, ItemError(code="invalid_field", field=field, message=first["msg"]))

        item = _clean(item)
        stored = self.activities.get(ext)
        if stored is None:
            if not item.get("action"):
                return _rejected(index, echo, ItemError(code="unknown_activity", field="action"))
            self.activities[ext] = {**item, "activityId": str(uuid.uuid4())}
            return ActivityResult(
                index=index, external_id=echo, result="created", activity_id=self.activities[ext]["activityId"]
            )

        if stored["status"] in FINAL_STATUSES and item["status"] not in FINAL_STATUSES:
            return ActivityResult(index=index, external_id=echo, result="stale", activity_id=stored["activityId"])

        warnings = []
        for key in _SET_ONCE:
            if key in item and stored.get(key) is not None:
                if item.pop(key) != stored[key]:
                    warnings.append(ItemWarning(code="immutable_field_ignored", field=key))
        if "metadata" in item:
            item["metadata"] = {**(stored.get("metadata") or {}), **item["metadata"]}
        stored.update(item)
        return ActivityResult(
            index=index, external_id=echo, result="updated", activity_id=stored["activityId"], warnings=warnings
        )


class FakeRhyme(_FakeStore):
    """In-memory `Rhyme`: records what was posted and applies the API's upsert rules."""

    def me(self) -> Me:
        return self._me()

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        return self._post(activities)

    def post_activity(self, activity: ActivityInput) -> ActivityResult:
        return self._post([activity]).results[0]


class AsyncFakeRhyme(_FakeStore):
    """In-memory `AsyncRhyme`: records what was posted and applies the API's upsert rules."""

    async def me(self) -> Me:
        return self._me()

    async def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        return self._post(activities)

    async def post_activity(self, activity: ActivityInput) -> ActivityResult:
        return self._post([activity]).results[0]


def _rejected(index: int, external_id: str | None, error: ItemError) -> ActivityResult:
    return ActivityResult(index=index, external_id=external_id, result="rejected", error=error, retryable=False)
