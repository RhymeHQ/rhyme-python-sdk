from __future__ import annotations

from typing import TYPE_CHECKING

from ._exceptions import BadRequestError, FlushError, PayloadTooLargeError
from .types import MAX_BATCH, ActivityBatchResult

if TYPE_CHECKING:
    from ._base_client import ActivityInput

# Errors a smaller batch can fix: a body over the size limit, more items than the key allows, or (locally) an item
# that can't be serialised. A single item that still fails is dropped, since it would block the buffer forever.
_SPLITTABLE = (BadRequestError, PayloadTooLargeError, TypeError, ValueError)


class BaseBuffer:
    """The pending list, batch size and failure policy shared by `ActivityBuffer` and `AsyncActivityBuffer`."""

    def __init__(self, *, max_items: int) -> None:
        if not 1 <= max_items <= MAX_BATCH:
            raise ValueError(f"max_items must be 1 to {MAX_BATCH}")
        self._max_items = max_items
        self._pending: list[ActivityInput] = []

    def __len__(self) -> int:
        return len(self._pending)

    def _posted(
        self, batch: list[ActivityInput], result: ActivityBatchResult, results: list[ActivityBatchResult]
    ) -> None:
        # Only the flush holding the lock removes items, always from the front, and `batch` is that front; adds only
        # append, so this removes exactly what was posted.
        del self._pending[: len(batch)]
        results.append(result)

    def _failed(
        self, batch: list[ActivityInput], err: Exception, results: list[ActivityBatchResult]
    ) -> list[list[ActivityInput]] | FlushError:
        """The halves to post instead, or the `FlushError` to raise (after dropping a lone unacceptable item)."""
        if isinstance(err, _SPLITTABLE):
            if len(batch) > 1:
                mid = len(batch) // 2
                return [batch[:mid], batch[mid:]]
            del self._pending[:1]
            return FlushError(f"Dropped an activity the API won't accept: {err}", results=results, dropped=batch)
        return FlushError(f"Flush stopped; unsent activities stay buffered: {err}", results=results)
