"""Read-only AI chat contracts. Numerical metrics stay server-owned."""

from datetime import date as Date
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.domain.runtime import Period
from app.schemas.common import ContractModel

MAX_QUESTION_CHARS = 500
MAX_TURN_CHARS = 2000
MAX_HISTORY_TURNS = 6
MAX_SUGGESTIONS = 3

Question = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUESTION_CHARS)
]
TurnText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TURN_CHARS)
]
Suggestion = Annotated[str, Field(min_length=1, max_length=160)]


class ChatTurn(ContractModel):
    """An earlier message. Browser-supplied, so it is context and never authority."""

    role: Literal["user", "assistant"]
    content: TurnText


class ChatRequest(ContractModel):
    message: Question
    history: Annotated[list[ChatTurn], Field(max_length=MAX_HISTORY_TURNS)] = Field(
        default_factory=list
    )
    date: Date | None = None
    period: Period = "day"


class ProviderChatReply(ContractModel):
    """Strict structured output requested from the provider."""

    metrics_digest: str
    on_topic: bool
    answer: Annotated[str, Field(min_length=1, max_length=MAX_TURN_CHARS)]
    suggested_questions: Annotated[list[Suggestion], Field(max_length=MAX_SUGGESTIONS)]


class ChatResponse(ContractModel):
    status: Literal["ready", "unavailable", "rate_limited"]
    message: str
    answer: str | None = None
    on_topic: bool | None = None
    suggested_questions: list[str] = Field(default_factory=list)
    generated_at: datetime | None = None
    metrics_as_of: datetime | None = None
    period: Period | None = None
    data_complete: bool | None = None
    model: str | None = None
    retry_after_seconds: int | None = None
