from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import ActivityBatchResult, ItemError

if TYPE_CHECKING:
    from ._base_client import ActivityInput
    from .types import DroppedActivity


class RhymeError(Exception):
    """Base class for API, network and configuration errors.

    Invalid arguments raise `ValueError` instead (pydantic's `ValidationError` is one).
    """


class APIConnectionError(RhymeError):
    """The request never got a response (DNS, TLS, connection reset, ...)."""


class APITimeoutError(APIConnectionError):
    """The request timed out."""


class APIStatusError(RhymeError):
    """The API answered with a 4xx or 5xx status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        request_id: str | None = None,
        retry_after: int | None = None,
        details: list[ItemError] | None = None,
        body: Any = None,
    ) -> None:
        super().__init__(
            f"{status_code} {code or 'error'}: {message}" + (f" (request {request_id})" if request_id else "")
        )
        self.message = message
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.retry_after = retry_after
        self.details = details or []
        self.body = body


class BadRequestError(APIStatusError):
    """400: `invalid_request` or `invalid_json`; see `details`."""


class AuthenticationError(APIStatusError):
    """401: missing, malformed, unknown or revoked key, or a disabled org."""


class PermissionDeniedError(APIStatusError):
    """403: the key lacks the scope for this call."""


class NotFoundError(APIStatusError):
    """404."""


class PayloadTooLargeError(APIStatusError):
    """413: the body is over 1 MB; split the batch."""


class UnsupportedMediaTypeError(APIStatusError):
    """415."""


class RateLimitError(APIStatusError):
    """429: `rate_limited` (per-minute) or `quota_exceeded` (daily)."""


class InternalServerError(APIStatusError):
    """5xx: safe to retry, since activities upsert by `externalId`."""


class PostAllError(RhymeError):
    """`post_all` stopped on an error splitting can't fix (network, 5xx, 429, auth); the error is `__cause__`.

    `batch_results` and `dropped` are as in `PostAllResult`, for what happened before it stopped. `unsent` holds every
    activity neither posted nor dropped, in input order, so you can retry exactly those.
    """

    def __init__(
        self,
        message: str,
        *,
        batch_results: list[ActivityBatchResult],
        dropped: list[DroppedActivity],
        unsent: list[ActivityInput],
    ) -> None:
        super().__init__(message)
        self.batch_results = batch_results
        self.dropped = dropped
        self.unsent = unsent


_BY_STATUS: dict[int, type[APIStatusError]] = {
    400: BadRequestError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    413: PayloadTooLargeError,
    415: UnsupportedMediaTypeError,
    429: RateLimitError,
}


def status_error_class(status_code: int) -> type[APIStatusError]:
    if status_code >= 500:
        return InternalServerError
    return _BY_STATUS.get(status_code, APIStatusError)
