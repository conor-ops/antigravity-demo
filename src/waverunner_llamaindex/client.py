import functools
import json
import os
import warnings
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
from dotenv import load_dotenv

from .models import (
    Config,
    EnvironmentConfig,
    RequestModel,
    ResponseModel,
    SetupRequestEnvironment,
    SetupRequestInlineSource,
    SetupRequestInput,
    SetupRequestModel,
    SetupRequestRepoSource,
)

SETUP_ENVIRONMENT_PROMPT = """
To set up your environment, run these exact commands:

```bash
# install bun
curl -fsSL https://bun.com/install | bash

# copy skills so that they are globally available
mkdir -p /.agents/skills
cp -r /llamaparse-agent-skills/skills/llamaparse /.agents/skills
cp -r /llamaparse-agent-skills/skills/liteparse /.agents/skills

# install necessary system dependencies
bun install -g @llamaindex/liteparse
apt-get update && apt-get install -y --no-install-recommends \
    libvips42 \
    libreoffice \
    imagemagick

# Create a repository where to process data with the llamaparse skill
cd /data
bun init --yes
bun add @llamaindex/llama-cloud
```

Once you ran all these commands, respond exactly with 'DONE'. If one or more commands failed, start you response with "FAILED", and report failures.
"""

CONFIG_PATH = ".waveconfig.json"


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

    async def setup_environment(self, should_send_api_key: bool = True) -> None:
        config = get_config()
        request = SetupRequestModel(
            input=[SetupRequestInput(text=SETUP_ENVIRONMENT_PROMPT)],
            environment=SetupRequestEnvironment(
                sources=[
                    SetupRequestRepoSource(
                        source=config.github.repository_url.replace(
                            "https://github.com/", "github://"
                        ),
                        target="/data",
                    )
                ]
            ),
        )
        if should_send_api_key:
            lc_api_key = get_lc_api_key()
            content = f"LLAMA_CLOUD_API_KEY={lc_api_key}\n"
            request.environment.sources.append(
                SetupRequestInlineSource(content=content, target="/data/.env")
            )

        async with self._get_client() as client:
            response = await client.post(
                "/interactions",
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": get_api_key(),
                },
                json=request.model_dump(),
            )
            response.raise_for_status()
            res_json = response.json()
            validated = ResponseModel.model_validate(res_json)
            if validated.status.lower() != "success":
                raise RuntimeError(
                    f"Agent setup exited with status: {validated.status}"
                )
            output_found = False
            for step in validated.steps:
                if step.type == "model_output":
                    output_found = True
                    text = ""
                    for c in step.content:
                        if c.type == "text":
                            text += c.text
                    if "DONE" in text:
                        break
                    if "FAILED" in text:
                        raise RuntimeError(f"Agent setup failed: {text}")
            if not output_found:
                warnings.warn(
                    "Agent did not produce a `model_output` step", RuntimeWarning
                )

            with open(CONFIG_PATH, "w") as f:
                config.environment = EnvironmentConfig(
                    id=validated.environment_id, has_api_key=should_send_api_key
                )
                json.dump(config.model_dump(), f, indent=2)
            print(
                f"Agent completed setup successfully and its environment has been saved at: `{CONFIG_PATH}`"
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
        )
        async with self._get_client() as client:
            response = await client.post(
                "/interactions",
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": get_api_key(),
                },
                json=request.model_dump(),
            )
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    payload = line[len("data:") :].strip()
                    if payload == "[DONE]":
                        break
                    print(json.loads(payload))
