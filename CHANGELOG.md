# Changelog

## 0.1.0 (unreleased)

- First release: `Rhyme` and `AsyncRhyme` clients for `GET /me` and `POST /activities`, built on HTTPX2. Typed models, retries with `Retry-After`, `ActivityBuffer` and `AsyncActivityBuffer` for batching, `rhyme.testing.FakeRhyme` and `AsyncFakeRhyme`, and the `RhymeClient` and `AsyncRhymeClient` protocols that the real and fake clients satisfy.
- Models refuse an explicit `None` on fields the API can't store as null, and integers above 2³¹−1.
- Buffers split a batch the API refuses as too large or invalid, drop a single activity it will never accept, and raise `FlushError` with the results already posted and the dropped activities.
- A malformed response body raises `RhymeError` instead of a pydantic error. Retries are logged on the `rhyme` logger.
