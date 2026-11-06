from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ._base_buffer import BaseBuffer
from ._exceptions import FlushError
from .types import MAX_BATCH, ActivityBatchResult

if TYPE_CHECKING:
    from ._base_client import ActivityInput
    from ._client import RhymeClient


class ActivityBuffer(BaseBuffer):
    """Collects activities and posts them in batches.

    A batch the API refuses as too large is split. An activity it refuses on its own is dropped and reported in
    `FlushError.dropped`; on any other failure, unsent items stay buffered. Safe to share between threads.

    The `with` block flushes on a clean exit only; if it raises, unsent items stay in the buffer (see `len()`).
    """

    def __init__(self, client: RhymeClient, *, max_items: int = MAX_BATCH) -> None:
        super().__init__(max_items=max_items)
        self._client = client
        # One flush at a time: otherwise a thread adding during another thread's post would send the same batch
        # again, and both would then delete items, dropping some that were never sent.
        self._flush_lock = threading.Lock()

    def __enter__(self) -> ActivityBuffer:
        return self

    def __exit__(self, exc_type: object, *exc: object) -> None:
        if exc_type is None:
            self.flush()

    def add(self, activity: ActivityInput) -> list[ActivityBatchResult]:
        """Buffer one activity, posting full batches; returns the results of any batches posted."""
        self._pending.append(activity)
        if len(self._pending) >= self._max_items:
            return self.flush()
        return []

    def flush(self) -> list[ActivityBatchResult]:
        """Post everything buffered, one batch at a time; raises `FlushError` if anything couldn't be posted."""
        results: list[ActivityBatchResult] = []
        with self._flush_lock:
            while self._pending:
                self._send(self._pending[: self._max_items], results)
        return results

    def _send(self, batch: list[ActivityInput], results: list[ActivityBatchResult]) -> None:
        try:
            result = self._client.post_activities(batch)
        except Exception as e:
            outcome = self._failed(batch, e, results)
            if isinstance(outcome, FlushError):
                raise outcome from e
            for half in outcome:
                self._send(half, results)
        else:
            self._posted(batch, result, results)
