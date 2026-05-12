# waverunner-llamaindex

A small CLI to set up and run a **WaveRunner** agent backed by your own GitHub
repository as a data source, with LlamaParse/LiteParse skills for
parsing unstructured files.

## Requirements

- Python `>=3.13`
- [`uv`](https://docs.astral.sh/uv/)
- [`gh`](https://cli.github.com/) authenticated (`gh auth login`)
- A Google/Gemini API key in `GOOGLE_API_KEY` or `GEMINI_API_KEY`
- (Optional) A LlamaCloud key in `LLAMA_CLOUD_API_KEY` or `LLAMA_PARSE_API_KEY`
  to enable LlamaParse

Environment variables can be set in a local `.env` file.

## Install

```bash
uv tool install .
```

This exposes the `wavellama` command.

## Usage

The CLI has three commands: `git-wiz`, `setup`, and `run`. They are meant to
be run in order — each one persists state in `.waveconfig.json` in the current
directory.

### 1. `wavellama git-wiz` — publish your data to GitHub

Initializes a git repo from a local directory, creates a GitHub repository,
pushes the contents, and saves the repository URL to `.waveconfig.json`.

```bash
wavellama git-wiz \
    --directory ./my-data \
    --owner my-github-user \
    --repo-name my-waverunner-data \
    --description "Data for my WaveRunner agent"
```

Flags:

| Flag | Description |
| --- | --- |
| `-d`, `--directory` | Local directory to publish (defaults to `.`) |
| `-o`, `--owner` | GitHub user/org that will own the repo |
| `-r`, `--repo-name` | Name of the GitHub repo to create |
| `--description` | Optional repo description |

### 2. `wavellama setup` — prepare the agent environment

Provisions the remote WaveRunner environment from the repository configured
above and saves the resulting environment id in `.waveconfig.json`.

```bash
wavellama setup                  # uses LlamaParse (sends LlamaCloud key)
wavellama setup --no-send-api-key   # LiteParse only, no key sent
```

### 3. `wavellama run` — run the agent

```bash
wavellama run --prompt "Summarize the PDFs in /data"
```

## Config file

`.waveconfig.json` is created and updated by the CLI. It looks like:

```json
{
  "github": { "repository_url": "https://github.com/owner/repo" },
  "environment": { "id": "...", "has_api_key": true }
}
```
