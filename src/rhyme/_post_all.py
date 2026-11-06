"""`post_all`: post any number of activities in batches, shared by the real and fake clients."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from itertools import batched
from typing import TYPE_CHECKING

from ._exceptions import BadRequestError, PayloadTooLargeError, PostAllError
from .types import MAX_BATCH, ActivityBatchResult, DroppedActivity, PostAllResult

if TYPE_CHECKING:
    from ._base_client import ActivityInput

# Errors a smaller batch can fix: a body over 1 MB (413), a body the API refuses as invalid (400), or (locally) an item
# that can't be serialised. A single item that still fails is dropped, since retrying it can't help.
_SPLITTABLE = (BadRequestError, PayloadTooLargeError, TypeError, ValueError)


class _Run:
    """The batches still to post and what has happened so far; the sync and async loops differ only in `await`."""

    def __init__(self, activities: Iterable[ActivityInput], max_items: int) -> None:
        if not 1 <= max_items <= MAX_BATCH:
            raise ValueError(f"max_items must be 1 to {MAX_BATCH}")
        self.batches = deque(list(b) for b in batched(activities, max_items))
        self.batch_results: list[ActivityBatchResult] = []
        self.dropped: list[DroppedActivity] = []

    def failed(self, batch: list[ActivityInput], err: Exception) -> None:
        """Queue the halves of `batch`, or drop it if it's one item; raises `PostAllError` if splitting can't help."""
        if not isinstance(err, _SPLITTABLE):
            unsent = batch + [a for b in self.batches for a in b]
            raise PostAllError(
                f"Stopped with {len(unsent)} activities unsent: {err}",
                batch_results=self.batch_results,
                dropped=self.dropped,
                unsent=unsent,
            ) from err
        if len(batch) == 1:
            self.dropped.append(DroppedActivity(activity=batch[0], error=err))
            return
        mid = len(batch) // 2
        self.batches.appendleft(batch[mid:])
        self.batches.appendleft(batch[:mid])

    def result(self) -> PostAllResult:
        return PostAllResult(batch_results=self.batch_results, dropped=self.dropped)


def post_all(
    post: Callable[[list[ActivityInput]], ActivityBatchResult], activities: Iterable[ActivityInput], max_items: int
) -> PostAllResult:
    run = _Run(activities, max_items)
    while run.batches:
        batch = run.batches.popleft()
        try:
            run.batch_results.append(post(batch))
        except Exception as e:
            run.failed(batch, e)
    return run.result()


async def apost_all(
    post: Callable[[list[ActivityInput]], Awaitable[ActivityBatchResult]],
    activities: Iterable[ActivityInput],
    max_items: int,
) -> PostAllResult:
    run = _Run(activities, max_items)
    while run.batches:
        batch = run.batches.popleft()
        try:
            run.batch_results.append(await post(batch))
        except Exception as e:
            run.failed(batch, e)
    return run.result()
