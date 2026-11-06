from __future__ import annotations

import asyncio
import logging

import httpx2
import pytest
from conftest import API_KEY, BASE_URL, batch_body, respond

from rhyme import (
    Activity,
    ActivityRequest,
    ActivityStatus,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncRhyme,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    Rhyme,
    RhymeError,
)


def _activity(**kwargs) -> Activity:
    return Activity(external_id="zd:1:a", status=ActivityStatus.DONE, action="draft_reply", **kwargs)


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("RHYME_API_KEY", API_KEY)
    monkeypatch.setenv("RHYME_BASE_URL", BASE_URL + "/")
    client = Rhyme()
    assert client.base_url == BASE_URL


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"base_url": BASE_URL}, "API key"),
        ({"api_key": API_KEY}, "base URL"),
        ({"api_key": API_KEY, "base_url": "http://api.example.test"}, "https"),
    ],
)
def test_config_errors(monkeypatch, kwargs, match):
    monkeypatch.delenv("RHYME_API_KEY", raising=False)
    monkeypatch.delenv("RHYME_BASE_URL", raising=False)
    with pytest.raises(RhymeError, match=match):
        Rhyme(**kwargs)


def test_http_allowed_for_localhost():
    assert Rhyme(api_key=API_KEY, base_url="http://localhost:8080/api/agent/v1").base_url.startswith("http://")


def test_repr_hides_key():
    assert API_KEY not in repr(Rhyme(api_key=API_KEY, base_url=BASE_URL))


def test_post_activities_sends_camel_case_and_omits_unset(make_client):
    client, transport = make_client(respond(200, batch_body("created")))
    result = client.post_activities(
        [_activity(request=ActivityRequest(source="zendesk", id="1"), duration_ms=1200, summary=None)]
    )

    call = transport.calls[0]
    assert call.method == "POST"
    assert str(call.url) == f"{BASE_URL}/activities"
    assert call.headers["Authorization"] == f"Bearer {API_KEY}"
    assert call.headers["User-Agent"].startswith("rhyme-python/")
    assert transport.body() == {
        "activities": [
            {
                "externalId": "zd:1:a",
                "status": "done",
                "action": "draft_reply",
                "request": {"source": "zendesk", "id": "1"},
                "durationMs": 1200,
                "summary": None,
            }
        ]
    }
    assert result.counts.created == 1
    assert result.rejected == []


def test_mappings_are_sent_as_given(make_client):
    client, transport = make_client(respond(200, batch_body("updated")))
    client.post_activity({"externalId": "x", "status": "pending"})
    assert transport.body() == {"activities": [{"externalId": "x", "status": "pending"}]}


@pytest.mark.parametrize("n", [0, 101])
def test_batch_bounds(make_client, n):
    client, _ = make_client()
    with pytest.raises(ValueError):
        client.post_activities([_activity()] * n)


def test_me(make_client):
    client, transport = make_client(
        respond(
            200,
            {
                "agent": {"id": "a", "name": "Agent"},
                "orgId": "o",
                "key": {"prefix": "rhyme_ak1_00000000", "name": "k", "scopes": ["activities:write"]},
                "limits": {
                    "maxItems": 100,
                    "maxBodyBytes": 1048576,
                    "requestsPerMinute": 120,
                    "itemsPerMinute": 1000,
                    "dailyItems": 20000,
                },
                "usage": {"day": "2026-11-02", "items": 5},
                "serverTime": "2026-11-02T04:43:08.488Z",
            },
        )
    )
    me = client.me()
    assert transport.calls[0].method == "GET"
    assert me.limits.daily_items == 20000
    assert me.usage.items == 5


def test_status_error_parsing(make_client):
    client, _ = make_client(
        respond(
            400,
            {
                "error": "Invalid request body",
                "code": "invalid_request",
                "requestId": "req_x",
                "details": [{"code": "invalid_field", "field": "activities", "message": "too many"}],
            },
        )
    )
    with pytest.raises(BadRequestError) as exc:
        client.post_activity(_activity())
    assert exc.value.code == "invalid_request"
    assert exc.value.request_id == "req_x"
    assert exc.value.details[0].field == "activities"


def test_auth_error_not_retried(make_client, sleeps):
    client, transport = make_client(respond(401, {"error": "Invalid API key", "code": "unauthorized"}))
    with pytest.raises(AuthenticationError):
        client.me()
    assert len(transport.calls) == 1
    assert sleeps == []


def test_rate_limit_retried_after_retry_after(make_client, sleeps):
    client, transport = make_client(
        respond(429, {"error": "Too many requests", "code": "rate_limited", "retryAfter": 3}),
        respond(200, batch_body("created")),
    )
    assert client.post_activity(_activity()).result == "created"
    assert sleeps == [3.0]
    assert len(transport.calls) == 2


def test_quota_exceeded_not_retried(make_client, sleeps):
    client, _ = make_client(
        respond(429, {"error": "Daily limit", "code": "quota_exceeded"}, headers={"Retry-After": "3600"})
    )
    with pytest.raises(RateLimitError) as exc:
        client.post_activity(_activity())
    assert exc.value.retry_after == 3600
    assert sleeps == []


def test_long_retry_after_not_waited(make_client, sleeps):
    client, _ = make_client(respond(429, {"code": "rate_limited"}, headers={"Retry-After": "120"}))
    with pytest.raises(RateLimitError):
        client.me()
    assert sleeps == []


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        ({"code": "rate_limited", "retryAfter": "abc"}, {}),
        ({"code": "rate_limited", "retryAfter": -5}, {}),
        ({"code": "rate_limited", "retryAfter": True}, {}),
        ({"code": "rate_limited"}, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
    ],
)
def test_malformed_retry_after_ignored(make_client, sleeps, body, headers):
    client, _ = make_client(respond(429, body, headers=headers), respond(200, batch_body("created")))
    assert client.post_activity(_activity()).result == "created"
    assert len(sleeps) == 1 and sleeps[0] <= 0.5  # jittered backoff, not the bad value


def test_retry_after_from_body_is_parsed_from_string_and_float(make_client, sleeps):
    client, _ = make_client(
        respond(429, {"code": "rate_limited", "retryAfter": "4"}),
        respond(429, {"code": "rate_limited", "retryAfter": 1.2}),
        respond(200, batch_body("created")),
    )
    client.post_activity(_activity())
    assert sleeps == [4.0, 2.0]


@pytest.mark.parametrize("results", [(), ("created", "created")])
def test_post_activity_needs_exactly_one_result(make_client, results):
    client, _ = make_client(respond(200, batch_body(*results)))
    with pytest.raises(RhymeError, match="results for 1 activity"):
        client.post_activity(_activity())


def test_server_error_retried_then_raised(make_client, sleeps):
    client, transport = make_client(*[respond(500, {"code": "internal_error"}, headers={"X-Request-Id": "r"})] * 3)
    with pytest.raises(InternalServerError) as exc:
        client.post_activity(_activity())
    assert exc.value.request_id == "r"
    assert len(transport.calls) == 3
    assert len(sleeps) == 2


def test_connection_errors_retried(make_client, sleeps):
    client, _ = make_client(
        httpx2.ConnectError("reset"),
        httpx2.ReadTimeout("slow"),
        respond(200, batch_body("updated")),
    )
    assert client.post_activity(_activity()).result == "updated"
    assert len(sleeps) == 2


def test_timeout_raised_when_retries_exhausted(make_client):
    client, _ = make_client(httpx2.ReadTimeout("slow"), max_retries=0)
    with pytest.raises(APITimeoutError):
        client.me()


def test_connection_error_type(make_client):
    client, _ = make_client(httpx2.ConnectError("reset"), max_retries=0)
    with pytest.raises(APIConnectionError):
        client.me()


def test_redirect_not_followed(make_client):
    client, transport = make_client(respond(302, headers={"Location": "https://elsewhere.test/"}))
    with pytest.raises(APIStatusError) as exc:
        client.me()
    assert exc.value.status_code == 302
    assert len(transport.calls) == 1


def test_non_json_success_body(make_client):
    client, _ = make_client(respond(200))
    with pytest.raises(RhymeError, match="non-JSON"):
        client.me()


def test_close_only_owned_http_client():
    owned = Rhyme(api_key=API_KEY, base_url=BASE_URL)
    owned.close()
    assert owned._http.is_closed

    http = httpx2.Client()
    with Rhyme(api_key=API_KEY, base_url=BASE_URL, http_client=http):
        pass
    assert not http.is_closed


def test_async_close_only_owned_http_client():
    async def run() -> None:
        owned = AsyncRhyme(api_key=API_KEY, base_url=BASE_URL)
        await owned.aclose()
        assert owned._http.is_closed

        http = httpx2.AsyncClient()
        async with AsyncRhyme(api_key=API_KEY, base_url=BASE_URL, http_client=http):
            pass
        assert not http.is_closed

    asyncio.run(run())


def test_unexpected_success_body_raises_rhyme_error(make_client):
    client, _ = make_client(respond(200, {"counts": "nope"}))
    with pytest.raises(RhymeError, match="unexpected body"):
        client.post_activity(_activity())


def test_malformed_details_do_not_hide_the_error(make_client):
    client, _ = make_client(
        respond(400, {"error": "bad", "code": "invalid_request", "details": [{"field": "x"}, "junk", {"code": "c"}]})
    )
    with pytest.raises(BadRequestError) as exc:
        client.post_activity(_activity())
    assert [d.code for d in exc.value.details] == ["c"]


def test_retry_is_logged(make_client, caplog):
    client, _ = make_client(
        respond(500, {"code": "internal_error"}, headers={"X-Request-Id": "req_r"}),
        respond(200, batch_body("created")),
    )
    with caplog.at_level(logging.INFO, logger="rhyme"):
        client.post_activity(_activity())
    [record] = caplog.records
    assert "Retrying POST /activities" in record.getMessage() and "req_r" in record.getMessage()
