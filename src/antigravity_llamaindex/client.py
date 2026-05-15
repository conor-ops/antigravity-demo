import functools
import json
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError
from rich.console import Console

from .display import StreamRenderer, render_raw_line
from .models import (
    Config,
    EnvironmentConfig,
    InteractionStartEvent,
    RequestModel,
    SetupRequestInput,
    SetupRequestModel,
    parse_stream_event,
)

CONFIG_PATH = ".waveconfig.json"


class EnvironmentAlreadyDefinedError(BaseException):
    def __init__(self, environment_id: str, last_interaction: str) -> None:
        self.environment_id = environment_id
        self.last_interaction = last_interaction

    def __repr__(self) -> str:
        return f"An environment is already defined in {CONFIG_PATH}.\nID: {self.environment_id}\nLast used for: {self.last_interaction}. Run `wavellama reset` to eliminate the current environment."

    def __str__(self) -> str:
        return f"An environment is already defined in {CONFIG_PATH}.\nID: {self.environment_id}\nLast used for: {self.last_interaction}. Run `wavellama reset` to eliminate the current environment."


def build_setup_prompt(github_repo_url: str, has_api_key: bool) -> str:
    api_key_step = ""
    llama_cloud_install = ""
    if has_api_key:
        lc_api_key = get_lc_api_key()
        api_key_step = f"""
# write the LlamaCloud API key into /data/.env
echo 'LLAMA_CLOUD_API_KEY={lc_api_key}' > /data/.env
"""
        llama_cloud_install = """
# install the llama-cloud client in the data directory for the llamaparse skill
cd /data
bun init --yes
bun add @llamaindex/llama-cloud
"""
        skills_copy = """git clone https://github.com/run-llama/llamaparse-agent-skills /llamaparse-agent-skills
cp -r /llamaparse-agent-skills/skills/llamaparse /.agents/skills
cp -r /llamaparse-agent-skills/skills/liteparse /.agents/skills"""
    else:
        skills_copy = "git clone https://github.com/run-llama/llamaparse-agent-skills /llamaparse-agent-skills\ncp -r /llamaparse-agent-skills/skills/liteparse /.agents/skills"

    return f"""
To set up your environment, run these exact commands:

```bash
# clone the user-provided GitHub repository into /data
mkdir -p /data
git clone {github_repo_url} /tmp/user-repo
cp -a /tmp/user-repo/. /data/
rm -rf /tmp/user-repo

# install bun
curl -fsSL https://bun.com/install | bash

# copy skills so that they are globally available
mkdir -p /.agents/skills
{skills_copy}

# install necessary system dependencies
bun install -g @llamaindex/liteparse
apt-get update && apt-get install -y --no-install-recommends \\
    libvips42 \\
    libreoffice \\
    imagemagick
{llama_cloud_install}{api_key_step}
```

Once you ran all these commands, respond exactly with 'DONE'. If one or more commands failed, start you response with "FAILED", and report failures.
"""


@functools.lru_cache(maxsize=1)
def get_api_key() -> str:
    load_dotenv(".env")
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key is None:
        api_key = os.getenv("GEMINI_API_KEY")
    if api_key is None:
        raise RuntimeError(
            "No GOOGLE_API_KEY/GEMINI_API_KEY available in the current environment"
        )
    return api_key


@functools.lru_cache(maxsize=1)
def get_lc_api_key() -> str:
    load_dotenv(".env")
    api_key = os.getenv("LLAMA_CLOUD_API_KEY")
    if api_key is None:
        api_key = os.getenv("LLAMA_PARSE_API_KEY")
    if api_key is None:
        raise RuntimeError(
            "No LLAMA_CLOUD_API_KEY/LLAMA_PARSE_API_KEY available in the current environment"
        )
    return api_key


def get_config() -> Config:
    with open(CONFIG_PATH, "r") as f:
        content = f.read()
        config = Config.model_validate_json(content)
        return config


def build_prompt(has_api_key: bool, user_input: str) -> str:
    if has_api_key:
        return f"""
<system>
@llamaindex/liteparse and the lit CLI are globally installed.
@llamaindex/llama-cloud is installed in the /data/ folder as a TS dependency.
Use the /data directory as your working directory (`cd /data`).
In the /data directory you will find all user-provided files you have access to.
If the user's request involves reading plain-text files, use the read_file tool.
If the user's request involves reading an unstructured file (PDF, Office document, image), read
the LlamaParse skill at `/.agents/skills/llamaparse/SKILL.md` and the associated
script `/.agents/skills/llamaparse/scripts/example.ts`
and use the LlamaParse cloud service to parse the file with the API key in /data/.env.
If the user explicitly requires quick and less accurate parsing, read the LiteParse skill
at `/.agents/skills/liteparse/SKILL.md` and use the `lit` CLI to parse the file.
</system>
<critical>
Never send the API key in /data/.env over message to the user. Treat it as a sensitive secret
</critical>
<user>
{user_input}
</user>
"""
    else:
        return f"""
<system>
@llamaindex/liteparse and the lit CLI are globally installed.
Use the /data directory as your working directory (`cd /data`).
In the /data directory you will find all user-provided files you have access to.
If the user's request involves reading plain-text files, use the read_file tool.
If the user's request involves reading an unstructured file (PDF, Office document, image),
read the LiteParse skill at `/.agents/skills/liteparse/SKILL.md` and use the `lit` CLI to
parse the file and extract its content.
</system>
<user>
{user_input}
</user>
"""


class WaverRunnerClient:
    def __init__(self) -> None:
        self._url = "https://generativelanguage.googleapis.com/v1beta"

    @asynccontextmanager
    async def _get_client(self) -> AsyncGenerator[httpx.AsyncClient]:
        async with httpx.AsyncClient(base_url=self._url, timeout=600) as client:
            yield client

    async def _consume_stream(
        self,
        response: httpx.Response,
        title: str,
        has_api_key: bool = False,
    ) -> None:
        console = Console()
        with StreamRenderer(console=console, title=title) as renderer:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:") :].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError:
                    render_raw_line(console, payload)
                    continue
                try:
                    event = parse_stream_event(data)
                except ValidationError:
                    render_raw_line(console, payload)
                    continue
                if isinstance(event, InteractionStartEvent):
                    conf = get_config()
                    if conf.environment is None:
                        conf.environment = EnvironmentConfig(
                            id=event.interaction.environment_id,
                            previous_interaction_id=event.interaction.id,
                            has_api_key=has_api_key,
                        )
                    else:
                        conf.environment.previous_interaction_id = event.interaction.id
                    with open(CONFIG_PATH, "w") as f:
                        json.dump(conf.model_dump(), f, indent=2)
                renderer.handle(event)

    async def setup_environment(self, should_send_api_key: bool = True) -> None:
        config = get_config()
        if config.environment is not None:
            raise EnvironmentAlreadyDefinedError(
                config.environment.id,
                config.environment.previous_interaction_id or "none",
            )
        prompt = build_setup_prompt(
            github_repo_url=config.github.repository_url,
            has_api_key=should_send_api_key,
        )
        request = SetupRequestModel(
            input=[SetupRequestInput(text=prompt)], environment="remote"
        )

        async with self._get_client() as client:
            async with client.stream(
                "POST",
                "/interactions",
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": get_api_key(),
                },
                json=request.model_dump(),
            ) as response:
                if response.status_code < 200 or response.status_code >= 299:
                    body = await response.aread()
                    print(response.status_code, body.decode(errors="replace"))
                    raise ValueError("An error occurred")
                await self._consume_stream(
                    response, title="Antigravity setup", has_api_key=should_send_api_key
                )

    async def send_request(self, prompt: str) -> None:
        config = get_config()
        if config.environment is None:
            raise RuntimeError(
                "You need to set up the environment before running tasks with the agent. Run `wavellama setup` and re-try."
            )
        full_prompt = build_prompt(
            config.environment.has_api_key,
            prompt,
        )
        request = RequestModel(
            input=[SetupRequestInput(text=full_prompt)],
            environment=config.environment.id,
            previous_interaction_id=config.environment.previous_interaction_id,
        )
        async with self._get_client() as client:
            async with client.stream(
                "POST",
                "/interactions",
                headers={
                    "x-goog-api-key": get_api_key(),
                },
                json=request.model_dump(exclude_none=True),
            ) as response:
                if response.status_code < 200 or response.status_code >= 299:
                    body = await response.aread()
                    print(response.status_code, body.decode(errors="replace"))
                    raise ValueError("An error occurred")
                await self._consume_stream(response, title="Antigravity")
