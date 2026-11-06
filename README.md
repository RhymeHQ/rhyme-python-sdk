# Rhyme Python SDK

Python client for the Rhyme Agent API. An agent reports what it did, one activity per ticket turn, and Rhyme shows it on the agent's Live Activity page.

```sh
pip install rhyme-sdk
```

Requires Python 3.12+.

## Quickstart

```python
from rhyme import Activity, ActivityRequest, Rhyme

client = Rhyme(api_key="rhyme_ak1_...", base_url="https://<rhyme-host>/api/agent/v1")

client.me()  # smoke-test the key: agent, scopes, limits, today's usage

result = client.post_activity(
    Activity(
        external_id="zendesk:4822:1",
        status="done",
        action="draft_reply",
        request=ActivityRequest(source="zendesk", id="4822"),
        summary="Drafted a reply about a delayed refund",
        duration_ms=8400,
    )
)
if not result.ok:
    print(result.error)
```

`api_key` and `base_url` fall back to the `RHYME_API_KEY` and `RHYME_BASE_URL` environment variables. There is no default base URL: you choose which Rhyme region your activity data goes to.

### Async

`AsyncRhyme` has the same methods, as coroutines:

```python
from rhyme import AsyncRhyme

async with AsyncRhyme() as client:
    result = await client.post_activity(activity)
```

### HTTP client

Both clients use [HTTPX2](https://github.com/pydantic/httpx2). To configure proxies, TLS or connection limits, pass your own `httpx2.Client` (or `httpx2.AsyncClient` for `AsyncRhyme`) as `http_client=`. The SDK doesn't close a client you pass in. Redirects aren't followed: a 3xx raises `APIStatusError`.

## Activities

`POST /activities` upserts by `external_id`, so posting the same id again updates the activity (for example `pending` → `done`).

- A field you **don't set** keeps its stored value.
- Setting a field to **`None`** clears it. That works for the text fields (`requester`, `ask`, `summary`, `outcome`, `escalation_reason`, `output`), `duration_ms`, `usage`, `tools` and a reference's `url`. Every other field can't be null: `Activity(action=None)` raises `ValueError`, so leave the field unset instead.
- `request`, `occurred_at`, `workflow`, `step` and `intent` are set once. A later, different value is ignored with an `immutable_field_ignored` warning. `metadata` is merged into the stored metadata.
- `done` and `handed_over` are final. A later `pending` or `failed` comes back with result `stale`, and nothing is written.
- A batch is 1–100 activities. Each item gets its own result, so a `200` can still contain rejected items:

```python
batch = client.post_activities(activities)
for item in batch.rejected:
    print(item.index, item.error.code, item.error.field)
```

You can also pass plain dicts in API field names (`{"externalId": ..., "status": ...}`). They are sent as given, without validation.

### Batching

`ActivityBuffer` posts full batches as you add activities, and the rest when it's flushed or its `with` block exits cleanly. Threads can share one buffer: only one batch is posted at a time, and each item is posted once.

```python
from rhyme import ActivityBuffer

with ActivityBuffer(client) as buffer:
    for activity in activities:
        buffer.add(activity)
```

With `AsyncRhyme`, use `AsyncActivityBuffer`. Tasks on the same event loop can share one buffer: only one batch is posted at a time, and each item is posted once.

```python
from rhyme import AsyncActivityBuffer

async with AsyncActivityBuffer(client) as buffer:
    for activity in activities:
        await buffer.add(activity)
```

When a post fails, `add()` and `flush()` raise `rhyme.FlushError`. The underlying error is its `__cause__`, and `.results` holds the batches that flush did post.

- If the API refuses a batch as too large (413) or invalid (400), the buffer splits it and posts the halves. An activity it still refuses on its own is removed from the buffer and returned in `.dropped`, so one bad item can't block the rest. A plain dict that can't be serialised to JSON is handled the same way.
- On any other error (network, 5xx, rate limits, auth), nothing is dropped. The unsent activities stay buffered for the next flush.
- If the `with` block raises, the buffer doesn't flush. The unsent activities stay in it (`len(buffer)`), so you can flush them yourself.

## Errors and retries

API, network and configuration errors raise a subclass of `rhyme.RhymeError`. Invalid arguments, such as a malformed `Activity` or an empty batch, raise `ValueError` (pydantic's `ValidationError` is one).

| Exception | When |
|---|---|
| `BadRequestError` (400) | Invalid body, e.g. not 1 to 100 activities; see `.details` |
| `AuthenticationError` (401) | Missing, unknown or revoked key |
| `PermissionDeniedError` (403) | The key lacks the scope |
| `PayloadTooLargeError` (413) | Body over 1 MB |
| `RateLimitError` (429) | `.code` is `rate_limited` or `quota_exceeded`; `.retry_after` in seconds |
| `InternalServerError` (5xx) | Server error |
| `APIConnectionError` / `APITimeoutError` | No response |

Every `APIStatusError` carries `.status_code`, `.code` and `.request_id`. Quote the request id when reporting a problem.

The client retries connection errors, timeouts, 5xx responses and `rate_limited` up to `max_retries` times (default 2). It honours `Retry-After` up to 60 seconds and backs off with jitter otherwise. It never retries `quota_exceeded` or other 4xx errors. Retrying is safe because activities upsert by `external_id`.

```python
client = Rhyme(max_retries=5, timeout=30)
```

Each retry is logged at `INFO` on the `rhyme` logger, with the error and its request id.

## Testing

`rhyme.testing.FakeRhyme` has the same methods as `Rhyme`, but sends nothing. It builds each request body exactly as `Rhyme` would and records it, then returns every item as `created`. It doesn't model the API: nothing is stored, merged or rejected.

```python
from rhyme.testing import FakeRhyme

fake = FakeRhyme()
my_agent.run(rhyme=fake)
assert fake.posted[-1]["status"] == "done"  # activities in API (camelCase) field names
assert len(fake.batches) == 1  # one entry per post call
```

Set `fake.error` to an exception to make the next call raise it, or `fake.result` to an `ActivityBatchResult` to make the next post return it (to test how your code handles rejected items, say). `fake.me_calls` counts `me()` calls. `rhyme.testing.AsyncFakeRhyme` does the same for `AsyncRhyme`.

Type your own code against the `RhymeClient` protocol (or `AsyncRhymeClient`), so it accepts either the real client or the fake:

```python
from rhyme import AsyncRhymeClient, RhymeClient


def report_turn(rhyme: RhymeClient, ticket_id: str) -> None: ...


async def report_turn_async(rhyme: AsyncRhymeClient, ticket_id: str) -> None: ...
```

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run pyright
pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Releasing

1. Bump `version` in `pyproject.toml` and add a `CHANGELOG.md` entry.
2. Publish a GitHub release tagged `vX.Y.Z`. The release workflow tests the code, builds it and uploads it to PyPI using trusted publishing.

## License

Apache-2.0. See [LICENSE](LICENSE).
