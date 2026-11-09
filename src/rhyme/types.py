"""Request and response models for the Rhyme Agent API v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

if TYPE_CHECKING:
    from ._base_client import ActivityInput

MAX_BATCH = 100
# The server stores integers as Postgres `integer`.
INT32_MAX = 2**31 - 1

MetadataScalar = str | int | float | bool | None
MetadataValue = MetadataScalar | list[MetadataScalar]


class _Request(BaseModel):
    """A request body. Fields left unset keep their stored value; fields set to None clear it."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="forbid",
    )

    def to_api(self) -> dict[str, Any]:
        """The JSON body for this model, omitting fields that were never set."""
        return self.model_dump(mode="json", exclude_unset=True)


class _Response(BaseModel):
    # Responses tolerate new fields so a server addition never breaks an installed SDK.
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
        extra="allow",
    )


class ActivityStatus(StrEnum):
    DONE = "done"
    PENDING = "pending"
    HANDED_OVER = "handed_over"
    FAILED = "failed"
    NO_ACTION = "no_action"


def _not_null(*fields: str) -> Any:
    """A validator for fields the API treats as optional but not nullable: leave them unset rather than `None`.

    Defaults aren't validated, so this only fires when `None` is passed explicitly.
    """

    def check(value: Any) -> Any:
        if value is None:
            raise ValueError("cannot be null; leave it unset instead")
        return value

    return field_validator(*fields, mode="before")(check)


ActivityStatusName = Literal["done", "pending", "handed_over", "failed", "no_action"]


class ActivityRequest(_Request):
    """The ticket an activity is about."""

    source: str = Field(max_length=40, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    id: str = Field(min_length=1, max_length=128)
    url: str | None = Field(default=None, max_length=2048)

    _reject_null = _not_null("url")


class WorkflowRef(_Request):
    """A workflow by `key` (optionally with `version`) or by workflow version `id`."""

    key: str | None = Field(default=None, min_length=1, max_length=200)
    version: int | None = Field(default=None, ge=1, le=INT32_MAX)
    id: str | None = None

    _reject_null = _not_null("key", "version", "id")

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.key is None) == (self.id is None):
            raise ValueError("set exactly one of key or id")
        if self.version is not None and self.key is None:
            raise ValueError("version needs key")
        return self


class StepRef(_Request):
    """A workflow step by `key` or `id`."""

    key: str | None = Field(default=None, min_length=1, max_length=200)
    id: str | None = None

    _reject_null = _not_null("key", "id")

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if (self.key is None) == (self.id is None):
            raise ValueError("set exactly one of key or id")
        return self


class Reference(_Request):
    system: str = Field(min_length=1, max_length=40)
    ref: str = Field(min_length=1, max_length=200)
    url: str | None = Field(default=None, max_length=2048)


class ModelUsage(_Request):
    model: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,99}$")
    input_tokens: int = Field(ge=0, le=INT32_MAX)
    output_tokens: int = Field(ge=0, le=INT32_MAX)
    cached_input_tokens: int | None = Field(default=None, ge=0, le=INT32_MAX)

    _reject_null = _not_null("cached_input_tokens")

    @model_validator(mode="after")
    def _cached_within_input(self) -> Self:
        if self.cached_input_tokens is not None and self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        return self


class ToolCall(_Request):
    name: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9._:/-]{0,99}$")
    ok: bool | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=INT32_MAX)

    _reject_null = _not_null("ok", "duration_ms")


class Activity(_Request):
    """One Live Activity row.

    Leave a field unset to keep its stored value. Only the text fields, `duration_ms`, `usage` and `tools` can be
    cleared with `None`; the others can't be null.
    """

    external_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,200}$")
    status: ActivityStatus | ActivityStatusName
    action: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    occurred_at: AwareDatetime | None = None
    request: ActivityRequest | None = None
    workflow: WorkflowRef | None = None
    step: StepRef | None = None
    intent: str | None = Field(default=None, min_length=1, max_length=100)
    requester: str | None = None
    ask: str | None = None
    summary: str | None = None
    outcome: str | None = None
    escalation_reason: str | None = None
    output: str | None = None
    duration_ms: int | None = Field(default=None, ge=0, le=INT32_MAX)
    references: list[Reference] | None = Field(default=None, max_length=20)
    usage: list[ModelUsage] | None = Field(default=None, max_length=20)
    tools: list[ToolCall] | None = Field(default=None, max_length=500)
    metadata: dict[str, MetadataValue] | None = None

    _reject_null = _not_null("action", "occurred_at", "request", "workflow", "step", "intent", "references", "metadata")


class ItemWarning(_Response):
    code: str
    field: str | None = None
    message: str | None = None


class ItemError(_Response):
    code: str
    field: str | None = None
    message: str | None = None
    sqlstate: str | None = None


class ResolvedLinks(_Response):
    workflow_id: str | None = None
    step_id: str | None = None
    intent_id: str | None = None


class ActivityResult(_Response):
    index: int
    external_id: str | None = None
    result: str
    activity_id: str | None = None
    resolved: ResolvedLinks | None = None
    warnings: list[ItemWarning] = Field(default_factory=list)
    error: ItemError | None = None
    retryable: bool | None = None

    @property
    def ok(self) -> bool:
        """True unless the item was rejected (`stale` counts as ok: nothing needed writing)."""
        return self.result != "rejected"


class BatchCounts(_Response):
    received: int
    created: int
    updated: int
    stale: int
    rejected: int


class ActivityBatchResult(_Response):
    request_id: str
    counts: BatchCounts
    results: list[ActivityResult]

    @property
    def rejected(self) -> list[ActivityResult]:
        return [r for r in self.results if not r.ok]


@dataclass(frozen=True)
class DroppedActivity:
    """An activity `post_all` gave up on: the API refused it on its own (400 or 413), or it isn't JSON-serialisable."""

    activity: ActivityInput
    error: Exception


@dataclass(frozen=True)
class PostAllResult:
    """What `post_all` did: one `ActivityBatchResult` per request, in order, and the activities it dropped.

    Each batch's `index` values count from 0 within that batch. Items the API rejected individually are in each batch's
    `rejected`, as with `post_activities`.
    """

    batch_results: list[ActivityBatchResult] = field(default_factory=list)
    dropped: list[DroppedActivity] = field(default_factory=list)


class MeAgent(_Response):
    id: str
    name: str


class MeKey(_Response):
    prefix: str
    name: str
    scopes: list[str]


class MeLimits(_Response):
    max_items: int
    max_body_bytes: int
    requests_per_minute: int
    items_per_minute: int
    daily_items: int


class MeUsage(_Response):
    day: date
    items: int


class Me(_Response):
    """The calling key's agent, org, scopes, limits and today's usage."""

    agent: MeAgent
    org_id: str
    key: MeKey
    limits: MeLimits
    usage: MeUsage
    server_time: datetime
