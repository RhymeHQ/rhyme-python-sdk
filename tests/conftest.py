from __future__ import annotations

import asyncio
import inspect
import json
from collections import deque
from typing import Any

import httpx2
import pytest

from rhyme import AsyncRhyme, Rhyme

BASE_URL = "https://api.example.test/api/agent/v1"
API_KEY = "rhyme_ak1_00000000_secret"


def respond(status_code: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx2.Response:
    if body is None:
        return httpx2.Response(status_code, headers=headers)
    return httpx2.Response(status_code, json=body, headers=headers)


class Transport:
    """Replays scripted responses (or raises scripted exceptions) and records each request."""

    def __init__(self, *responses: httpx2.Response | Exception):
        self.responses = deque(responses)
        self.calls: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.calls.append(request)
        nxt = self.responses.popleft()
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def body(self, i: int = 0) -> Any:
        return json.loads(self.calls[i].content)


class Blocking:
    """Runs an `AsyncRhyme`'s coroutine methods to completion, so one test body covers both clients.

    Every call runs on the same event loop, as in real use: an `httpx2.AsyncClient` must not move between loops.
    """

    def __init__(self, client: AsyncRhyme, runner: asyncio.Runner):
        self._client = client
        self._runner = runner

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if inspect.iscoroutinefunction(attr):
            return lambda *args, **kwargs: self._runner.run(attr(*args, **kwargs))
        return attr


def batch_body(*results: str) -> dict[str, Any]:
    return {
        "requestId": "req_1",
        "counts": {
            "received": len(results),
            "created": results.count("created"),
            "updated": results.count("updated"),
            "stale": results.count("stale"),
            "rejected": results.count("rejected"),
        },
        "results": [
            {
                "index": i,
                "externalId": f"a{i}",
                "result": r,
                "activityId": None,
                "resolved": None,
                "warnings": [],
                "error": None,
            }
            for i, r in enumerate(results)
        ],
    }


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture(params=["sync", "async"])
def make_client(request, sleeps):
    """Builds a `Rhyme`, or an `AsyncRhyme` wrapped in `Blocking`, over a scripted transport."""

    async def async_sleep(delay: float) -> None:
        sleeps.append(delay)

    runner = asyncio.Runner()
    request.addfinalizer(runner.close)

    def _make(*responses: httpx2.Response | Exception, **kwargs: Any) -> tuple[Any, Transport]:
        transport = Transport(*responses)
        mock = httpx2.MockTransport(transport)
        if request.param == "sync":
            http = httpx2.Client(transport=mock)
            return Rhyme(api_key=API_KEY, base_url=BASE_URL, http_client=http, sleep=sleeps.append, **kwargs), transport
        http = httpx2.AsyncClient(transport=mock)
        client = AsyncRhyme(api_key=API_KEY, base_url=BASE_URL, http_client=http, sleep=async_sleep, **kwargs)
        return Blocking(client, runner), transport

    return _make
