from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ._base_buffer import BaseBuffer
from ._exceptions import FlushError
from .types import MAX_BATCH, ActivityBatchResult

if TYPE_CHECKING:
    from ._async_client import AsyncRhymeClient
    from ._base_client import ActivityInput


class AsyncActivityBuffer(BaseBuffer):
    """`ActivityBuffer` for `AsyncRhyme`, with the same failure handling; safe to share between tasks on one loop."""

    def __init__(self, client: AsyncRhymeClient, *, max_items: int = MAX_BATCH) -> None:
        super().__init__(max_items=max_items)
        self._client = client
        # One flush at a time: otherwise a task adding during another task's post would send the same batch again.
        self._flush_lock = asyncio.Lock()

    async def __aenter__(self) -> AsyncActivityBuffer:
        return self

    async def __aexit__(self, exc_type: object, *exc: object) -> None:
        if exc_type is None:
            await self.flush()

    async def add(self, activity: ActivityInput) -> list[ActivityBatchResult]:
        """Buffer one activity, posting full batches; returns the results of any batches posted."""
        self._pending.append(activity)
        if len(self._pending) >= self._max_items:
            return await self.flush()
        return []

    async def flush(self) -> list[ActivityBatchResult]:
        """Post everything buffered, one batch at a time; raises `FlushError` if anything couldn't be posted."""
        results: list[ActivityBatchResult] = []
        async with self._flush_lock:
            while self._pending:
                await self._send(self._pending[: self._max_items], results)
        return results

    async def _send(self, batch: list[ActivityInput], results: list[ActivityBatchResult]) -> None:
        try:
            result = await self._client.post_activities(batch)
        except Exception as e:
            outcome = self._failed(batch, e, results)
            if isinstance(outcome, FlushError):
                raise outcome from e
            for half in outcome:
                await self._send(half, results)
        else:
            self._posted(batch, result, results)
