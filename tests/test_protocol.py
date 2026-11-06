from __future__ import annotations

import asyncio

from conftest import API_KEY, BASE_URL

from rhyme import AsyncRhyme, AsyncRhymeClient, Rhyme, RhymeClient
from rhyme.testing import AsyncFakeRhyme, FakeRhyme

# Type-checked in CI (pyright): if a client drifts from its protocol, these assignments fail.
_real: RhymeClient = Rhyme(api_key=API_KEY, base_url=BASE_URL)
_fake: RhymeClient = FakeRhyme()
_async_real: AsyncRhymeClient = AsyncRhyme(api_key=API_KEY, base_url=BASE_URL)
_async_fake: AsyncRhymeClient = AsyncFakeRhyme()


def _report(client: RhymeClient) -> str:
    return client.post_activity({"externalId": "t1", "status": "done", "action": "reply"}).result


async def _async_report(client: AsyncRhymeClient) -> str:
    return (await client.post_activity({"externalId": "t1", "status": "done", "action": "reply"})).result


def test_fake_usable_where_client_expected():
    assert _report(FakeRhyme()) == "created"


def test_async_fake_usable_where_client_expected():
    fake = AsyncFakeRhyme()
    assert asyncio.run(_async_report(fake)) == "created"
    assert fake.posted[0]["status"] == "done"
