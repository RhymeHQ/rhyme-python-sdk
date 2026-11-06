from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any, Protocol

import httpx2

from ._base_client import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT,
    ActivityInput,
    BaseClient,
    activity_payloads,
    json_body,
    parse_body,
    status_error,
    transport_error,
)
from .types import ActivityBatchResult, ActivityResult, Me


class RhymeClient(Protocol):
    """What `Rhyme` and `rhyme.testing.FakeRhyme` both provide; annotate with this to accept either."""

    def me(self) -> Me: ...

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult: ...

    def post_activity(self, activity: ActivityInput) -> ActivityResult: ...


class Rhyme(BaseClient):
    """Client for the Rhyme Agent API (`GET /me`, `POST /activities`)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: httpx2.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=max_retries)
        self._owns_http = http_client is None
        self._http = http_client or httpx2.Client()
        self._sleep = sleep

    def __enter__(self) -> Rhyme:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the HTTP client, unless it was passed in."""
        if self._owns_http:
            self._http.close()

    def me(self) -> Me:
        """Describe the calling key, its limits and today's usage; a good smoke test for a new key."""
        return parse_body(Me, self._request("GET", "/me"), "GET", "/me")

    def post_activities(self, activities: Iterable[ActivityInput]) -> ActivityBatchResult:
        """Upsert 1–100 activities. Rejected items come back in the result, not as an exception."""
        body = {"activities": activity_payloads(activities)}
        return parse_body(ActivityBatchResult, self._request("POST", "/activities", body), "POST", "/activities")

    def post_activity(self, activity: ActivityInput) -> ActivityResult:
        """Upsert one activity and return its item result."""
        return self.post_activities([activity]).results[0]

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        attempt = 0
        while True:
            try:
                resp = self._http.request(
                    method, f"{self.base_url}{path}", json=body, headers=self._headers, timeout=self.timeout
                )
            except httpx2.RequestError as e:
                err = transport_error(method, path, e)
            else:
                if resp.is_success:
                    return json_body(method, path, resp)
                err = status_error(resp)

            delay = self._retry_delay(err, attempt)
            if delay is None:
                raise err
            self._log_retry(method, path, delay, attempt, err)
            self._sleep(delay)
            attempt += 1
