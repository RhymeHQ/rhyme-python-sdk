# Changelog

## 0.1.0 (unreleased)

- First release: `Rhyme` and `AsyncRhyme` clients for `GET /me` and `POST /activities`, built on HTTPX2. Typed models, retries with `Retry-After`, `post_all` for posting any number of activities in batches, `rhyme.testing.FakeRhyme` and `AsyncFakeRhyme`, and the `RhymeClient` and `AsyncRhymeClient` protocols that the real and fake clients satisfy.
- Models refuse an explicit `None` on fields the API can't store as null, and integers above 2³¹−1.
- `post_all` splits a batch the API refuses as too large or invalid and returns any single activity it will never accept in `dropped`. On any other error it raises `PostAllError` with the batches already posted and the activities still unsent.
- A malformed response body (including a `post_activity` response without exactly one result) raises `RhymeError` instead of a pydantic error or `IndexError`; a malformed `retryAfter` is ignored. Retries are logged on the `rhyme` logger.
