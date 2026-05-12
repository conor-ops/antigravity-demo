from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field


class GitHubConfig(BaseModel):
    repository_url: str


class EnvironmentConfig(BaseModel):
    id: str
    has_api_key: bool


class Config(BaseModel):
    github: GitHubConfig
    environment: EnvironmentConfig | None = None


class SetupRequestRepoSource(BaseModel):
    type: Literal["repository"] = "repository"
    source: str
    target: str


class SetupRequestInlineSource(BaseModel):
    type: Literal["inline"] = "inline"
    content: str
    target: str


SetupRequestSource = Annotated[
    SetupRequestRepoSource | SetupRequestInlineSource, Discriminator("type")
]


class SetupRequestInput(BaseModel):
    type: Literal["text"] = "text"
    text: str


class SetupRequestEnvironment(BaseModel):
    type: Literal["remote"] = "remote"
    sources: list[SetupRequestSource]


class SetupRequestModel(BaseModel):
    agent: Literal["waverunner"] = "waverunner"
    environment: SetupRequestEnvironment
    input: list[SetupRequestInput]
    stream: Literal[False] = False


class RequestModel(BaseModel):
    agent: Literal["waverunner"] = "waverunner"
    input: list[SetupRequestInput]
    environment: str
    stream: Literal[True] = True


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
