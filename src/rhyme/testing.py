"""Stand-ins for `rhyme.Rhyme` and `rhyme.AsyncRhyme` that record what would be sent, for testing code that reports
activities.

The fakes build each request body exactly as the real clients do, record it and return a canned response. They don't
model the API: every item comes back `created` unless you set `result`, and nothing is stored or merged.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ._base_client import ActivityInput, activity_payloads, only_result
from ._post_all import apost_all, post_all
from .types import (
    MAX_BATCH,
    ActivityBatchResult,
    ActivityResult,
    BatchCounts,
    Me,
    MeAgent,
    MeKey,
    MeLimits,
    MeUsage,
    PostAllResult,
    ResolvedLinks,
)


class _Recorder:
    """Records each call and returns a canned response."""

    def __init__(self, *, me: Me | None = None) -> None:
        # One entry per `post_activities` / `post_activity` call: the activities as JSON, in API (camelCase) names.
        self.batches: list[list[dict[str, Any]]] = []
        self.me_calls = 0
        # Raised by the next call instead of recording it.
        self.error: Exception | None = None
        # Returned by the next post instead of the default all-`created` result.
        self.result: ActivityBatchResult | None = None
        self.me_result = me or Me(
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

    @property
    def posted(self) -> list[dict[str, Any]]:
        """Every activity posted, across all calls, in order."""
        return [item for batch in self.batches for item in batch]

    def _me(self) -> Me:
        self._raise_pending_error()
        self.me_calls += 1
        return self.me_result

    def _post(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        self._raise_pending_error()
        items = activity_payloads(activities)
        self.batches.append(items)
        if self.result is not None:
            result, self.result = self.result, None
            return result
        return ActivityBatchResult(
            request_id=f"req_fake_{next(self._request_ids)}",
            counts=BatchCounts(received=len(items), created=len(items), updated=0, stale=0, rejected=0),
            results=[
                ActivityResult(
                    index=i,
                    external_id=item.get("externalId"),
                    result="created",
                    activity_id=str(uuid.uuid4()),
                    resolved=ResolvedLinks(),
                )
                for i, item in enumerate(items)
            ],
        )

    def _raise_pending_error(self) -> None:
        if self.error is not None:
            err, self.error = self.error, None
            raise err


class FakeRhyme(_Recorder):
    """Recording `Rhyme`: see `batches`, `posted` and `me_calls`; set `error` or `result` to script the next call."""

    def me(self) -> Me:
        return self._me()

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        return self._post(activities)

    def post_activity(self, activity: ActivityInput) -> ActivityResult:
        return only_result(self._post([activity]))

    def post_all(self, activities: Iterable[ActivityInput], *, max_items: int = MAX_BATCH) -> PostAllResult:
        return post_all(self.post_activities, activities, max_items)


class AsyncFakeRhyme(_Recorder):
    """Recording `AsyncRhyme`: see `batches`, `posted` and `me_calls`; set `error` or `result` to script the next call."""

    async def me(self) -> Me:
        return self._me()

    async def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        return self._post(activities)

    async def post_activity(self, activity: ActivityInput) -> ActivityResult:
        return only_result(self._post([activity]))

    async def post_all(self, activities: Iterable[ActivityInput], *, max_items: int = MAX_BATCH) -> PostAllResult:
        return await apost_all(self.post_activities, activities, max_items)
