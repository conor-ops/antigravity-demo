import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from typer import Typer

from .client import CONFIG_PATH, WaverRunnerClient, get_config
from .gitrepo import init_and_push
from .models import Config, GitHubConfig

app = Typer()


@app.command(
    name="git-wiz",
    help="Setup the GitHub repository that will be used as data source for your Antigravity agent. `git` and the `gh` CLI are necessary for this step.",
)
def git_wiz(
    directory: Annotated[
        Path,
        typer.Option(
            "--directory",
            "-d",
            help="Path to the directory containing the data to publish.",
        ),
    ],
    owner: Annotated[
        str,
        typer.Option(
            "--owner",
            "-o",
            help="Org/user to create the GitHub repo under.",
        ),
    ],
    repo_name: Annotated[
        str,
        typer.Option(
            "--repo-name",
            "-r",
            help="Name of the GitHub repository to create.",
        ),
    ],
    description: Annotated[
        str | None,
        typer.Option(
            "--description",
            help="Optional description for the GitHub repo.",
        ),
    ] = None,
) -> None:
    url = init_and_push(
        directory=directory,
        owner=owner,
        repo_name=repo_name,
        description=description,
    )

    config_file = Path(CONFIG_PATH)
    if config_file.exists():
        config = Config.model_validate_json(config_file.read_text())
        config.github = GitHubConfig(repository_url=url)
    else:
        config = Config(github=GitHubConfig(repository_url=url))

    with open(config_file, "w") as f:
        json.dump(config.model_dump(), f, indent=2)

    typer.echo(f"Repository created at {url} and saved to `{CONFIG_PATH}`")


@app.command(
    name="setup",
    help="Setup and save the environment in which your Antigravity agent will run",
)
def setup_agent(
    send_api_key: Annotated[
        bool,
        typer.Option(
            "--send-api-key/--no-send-api-key",
            help="Whether to send the LlamaCloud API key to the agent environment. True by default.",
        ),
    ] = True,
) -> None:
    client = WaverRunnerClient()
    asyncio.run(client.setup_environment(should_send_api_key=send_api_key))


@app.command(name="run", help="Run your Antigravity agent with a specific prompt")
def run_agent(
    prompt: Annotated[
        str,
        typer.Option(
            "--prompt",
            "-p",
            help="The prompt to send to your Antigravity agent.",
        ),
    ],
) -> None:
    client = WaverRunnerClient()
    asyncio.run(client.send_request(prompt=prompt))


@app.command(
    name="reset", help="Eliminate the current environment to create a new one."
)
def reset() -> None:
    config = get_config()
    config.environment = None
    with open(CONFIG_PATH, "w") as f:
        json.dump(config.model_dump(), f, indent=2)
