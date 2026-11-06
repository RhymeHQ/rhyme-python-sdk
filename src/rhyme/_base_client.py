"""Configuration, payloads, retry policy and error mapping shared by `Rhyme` and `AsyncRhyme`."""

from __future__ import annotations

import logging
import math
import os
import random
from collections.abc import Iterable, Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlparse

import httpx2
from pydantic import BaseModel, ValidationError

from ._exceptions import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
    RhymeError,
    status_error_class,
)
from .types import MAX_BATCH, Activity, ActivityBatchResult, ActivityResult, ItemError

logger = logging.getLogger("rhyme")

DEFAULT_TIMEOUT = 10.0
DEFAULT_MAX_RETRIES = 2
# Longer waits are surfaced to the caller instead of blocking (a `quota_exceeded` waits until 00:00 UTC).
MAX_RETRY_WAIT = 60.0

ActivityInput = Activity | Mapping[str, Any]

try:
    _VERSION = version("rhyme-sdk")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    _VERSION = "0+unknown"


def activity_payload(activity: ActivityInput) -> dict[str, Any]:
    """The JSON body for one activity; mappings are sent as given, in API (camelCase) field names."""
    return activity.to_api() if isinstance(activity, Activity) else dict(activity)


def activity_payloads(activities: Iterable[ActivityInput]) -> list[dict[str, Any]]:
    """The JSON items for one `POST /activities`, checking the 1–100 item bound.

    Builds a list, so a generator is read once and every retry resends the same items.
    """
    items = [activity_payload(a) for a in activities]
    if not 1 <= len(items) <= MAX_BATCH:
        raise ValueError(f"post_activities takes 1 to {MAX_BATCH} activities, got {len(items)}")
    return items


def _is_local(host: str | None) -> bool:
    return host in {"localhost", "127.0.0.1", "::1"}


class BaseClient:
    """Configuration, headers and retry policy shared by the sync and async clients."""

    def __init__(self, *, api_key: str | None, base_url: str | None, timeout: float, max_retries: int) -> None:
        api_key = (api_key or os.environ.get("RHYME_API_KEY", "")).strip()
        if not api_key:
            raise RhymeError("No API key: pass api_key= or set RHYME_API_KEY")
        # No default on purpose: where activity data is sent (and its region) is the caller's decision.
        base_url = (base_url or os.environ.get("RHYME_BASE_URL", "")).strip()
        if not base_url:
            raise RhymeError("No base URL: pass base_url= or set RHYME_BASE_URL, e.g. https://<host>/api/agent/v1")
        parsed = urlparse(base_url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and _is_local(parsed.hostname)):
            raise RhymeError("base_url must use https (http is allowed only for localhost)")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": f"rhyme-python/{_VERSION}",
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(base_url={self.base_url!r})"

    def _retry_delay(self, err: RhymeError, attempt: int) -> float | None:
        if attempt >= self.max_retries:
            return None
        if isinstance(err, APIStatusError):
            if isinstance(err, RateLimitError) and err.code == "quota_exceeded":
                return None
            if err.status_code != 429 and err.status_code < 500:
                return None
            if err.retry_after is not None:
                return float(err.retry_after) if err.retry_after <= MAX_RETRY_WAIT else None
        return min(0.5 * 2**attempt, 8.0) * (0.5 + random.random() / 2)

    def _log_retry(self, method: str, path: str, delay: float, attempt: int, err: RhymeError) -> None:
        logger.info(
            "Retrying %s %s in %.1fs (retry %d of %d): %s", method, path, delay, attempt + 1, self.max_retries, err
        )


def transport_error(method: str, path: str, e: httpx2.RequestError) -> RhymeError:
    if isinstance(e, httpx2.TimeoutException):
        err: RhymeError = APITimeoutError(f"{method} {path} timed out")
    else:
        err = APIConnectionError(f"{method} {path} failed: {e}")
    err.__cause__ = e
    return err


def json_body(method: str, path: str, resp: httpx2.Response) -> Any:
    try:
        return resp.json()
    except ValueError as e:
        raise RhymeError(f"{method} {path} returned a non-JSON body") from e


def parse_body[M: BaseModel](model: type[M], data: Any, method: str, path: str) -> M:
    """Validate a 2xx body, raising `RhymeError` (not pydantic's error) if it isn't the expected shape."""
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise RhymeError(f"{method} {path} returned an unexpected body") from e


def only_result(batch: ActivityBatchResult) -> ActivityResult:
    """The item result of a one-activity post."""
    if len(batch.results) != 1:
        raise RhymeError(f"POST /activities returned {len(batch.results)} results for 1 activity")
    return batch.results[0]


def _retry_after(value: Any) -> int | None:
    """Seconds from a `retryAfter` field or `Retry-After` header; None if missing or not a non-negative number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        return int(value) if value.isdigit() else None
    if isinstance(value, int | float) and value >= 0 and value != float("inf"):
        return math.ceil(value)
    return None


def _item_errors(raw: Any) -> list[ItemError]:
    errors = []
    for d in raw if isinstance(raw, list) else []:
        try:
            errors.append(ItemError.model_validate(d))
        except ValidationError:
            continue  # a malformed detail must not hide the error it belongs to
    return errors


def status_error(resp: httpx2.Response) -> APIStatusError:
    try:
        body = resp.json()
    except ValueError:
        body = None
    data = body if isinstance(body, dict) else {}
    retry_after = _retry_after(data.get("retryAfter"))
    if retry_after is None:
        retry_after = _retry_after(resp.headers.get("Retry-After"))
    details = _item_errors(data.get("details"))
    return status_error_class(resp.status_code)(
        data.get("error") or resp.reason_phrase or "request failed",
        status_code=resp.status_code,
        code=data.get("code"),
        request_id=data.get("requestId") or resp.headers.get("X-Request-Id"),
        retry_after=retry_after,
        details=details,
        body=body,
    )
