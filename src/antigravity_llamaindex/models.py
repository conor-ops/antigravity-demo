from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field


class GitHubConfig(BaseModel):
    repository_url: str


class EnvironmentConfig(BaseModel):
    id: str
    has_api_key: bool
    previous_interaction_id: str | None = None


class Config(BaseModel):
    github: GitHubConfig
    environment: EnvironmentConfig | None = None


class SetupRequestInput(BaseModel):
    type: Literal["text"] = "text"
    text: str


class SetupRequestModel(BaseModel):
    agent: Literal["antigravity-preview-05-2026"] = "antigravity-preview-05-2026"
    environment: Literal["remote"] = "remote"
    input: list[SetupRequestInput]
    stream: Literal[True] = True


class RequestModel(BaseModel):
    agent: Literal["antigravity-preview-05-2026"] = "antigravity-preview-05-2026"
    input: list[SetupRequestInput]
    environment: str
    stream: Literal[True] = True
    previous_interaction_id: str | None = None


class ResponseStepOutput(BaseModel):
    type: str
    text: str = ""

    model_config = ConfigDict(extra="allow")


class ResponseStep(BaseModel):
    type: str
    content: list[ResponseStepOutput] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class ResponseModel(BaseModel):
    id: str
    agent: str
    environment_id: str
    status: str
    object: str
    steps: list[ResponseStep]


# ---------------------------------------------------------------------------
# Streaming event models
# ---------------------------------------------------------------------------


class _AllowExtra(BaseModel):
    model_config = ConfigDict(extra="allow")


class InteractionSummary(_AllowExtra):
    id: str
    status: str
    environment_id: str
    object: str | None = None
    agent: str | None = None


class TextOutput(_AllowExtra):
    type: Literal["text"] = "text"
    text: str = ""


class Usage(_AllowExtra):
    total_tokens: int | None = None
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_cached_tokens: int | None = None
    total_tool_use_tokens: int | None = None
    total_thought_tokens: int | None = None


class CompletedInteraction(InteractionSummary):
    outputs: list[TextOutput] = Field(default_factory=list)
    usage: Usage | None = None


# --- Content "start" payloads -----------------------------------------------


class FunctionCallContent(BaseModel):
    type: Literal["function_call"] = "function_call"
    id: str | None = None


class FunctionResultContent(BaseModel):
    type: Literal["function_result"] = "function_result"
    id: str | None = None


class ThoughtContent(BaseModel):
    type: Literal["thought"] = "thought"
    id: str | None = None


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    id: str | None = None


ContentStartPayload = Annotated[
    Union[FunctionCallContent, FunctionResultContent, ThoughtContent, TextContent],
    Discriminator("type"),
]


# --- Content delta payloads -------------------------------------------------


class FunctionCallDelta(_AllowExtra):
    type: Literal["function_call"] = "function_call"
    id: str | None = None
    name: str | None = None
    arguments: dict[str, Any] | str | None = None


class FunctionResultDelta(_AllowExtra):
    type: Literal["function_result"] = "function_result"
    id: str | None = None
    name: str | None = None
    result: Any | None = None


class ThoughtInnerText(_AllowExtra):
    type: Literal["text"] = "text"
    text: str = ""


class ThoughtSummaryDelta(_AllowExtra):
    type: Literal["thought_summary"] = "thought_summary"
    content: ThoughtInnerText


class TextDelta(_AllowExtra):
    type: Literal["text"] = "text"
    text: str = ""


ContentDeltaPayload = Annotated[
    Union[FunctionCallDelta, FunctionResultDelta, ThoughtSummaryDelta, TextDelta],
    Discriminator("type"),
]


# --- Top-level stream events ------------------------------------------------


class InteractionStartEvent(BaseModel):
    event_type: Literal["interaction.start"]
    interaction: InteractionSummary


class InteractionStatusUpdateEvent(BaseModel):
    event_type: Literal["interaction.status_update"]
    interaction_id: str
    status: str


class ContentStartEvent(BaseModel):
    event_type: Literal["content.start"]
    index: int
    content: ContentStartPayload


class ContentDeltaEvent(BaseModel):
    event_type: Literal["content.delta"]
    index: int
    delta: ContentDeltaPayload


class ContentStopEvent(BaseModel):
    event_type: Literal["content.stop"]
    index: int


class InteractionCompleteEvent(BaseModel):
    event_type: Literal["interaction.complete"]
    interaction: CompletedInteraction


StreamEvent = Annotated[
    Union[
        InteractionStartEvent,
        InteractionStatusUpdateEvent,
        ContentStartEvent,
        ContentDeltaEvent,
        ContentStopEvent,
        InteractionCompleteEvent,
    ],
    Discriminator("event_type"),
]


class _StreamEventEnvelope(BaseModel):
    event: StreamEvent


def parse_stream_event(payload: dict[str, Any]) -> StreamEvent:
    """Parse a single decoded JSON payload into a typed stream event."""
    return _StreamEventEnvelope.model_validate({"event": payload}).event
