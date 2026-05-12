import json
import subprocess
from pathlib import Path


class GitRepoError(RuntimeError):
    pass


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            check=check,
            text=True,
            capture_output=capture,
        )
    except FileNotFoundError as e:
        raise GitRepoError(
            f"Required executable not found: {cmd[0]}. Make sure it is installed and on PATH."
        ) from e
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        stdout = (e.stdout or "").strip()
        raise GitRepoError(
            f"Command `{' '.join(cmd)}` failed with exit code {e.returncode}.\n"
            f"stdout: {stdout}\nstderr: {stderr}"
        ) from e
    return result


def _ensure_gh_authenticated() -> None:
    try:
        _run(["gh", "auth", "status"])
    except GitRepoError as e:
        raise GitRepoError(
            "GitHub CLI is not authenticated. Run `gh auth login` first."
        ) from e


def init_and_push(
    directory: str | Path,
    owner: str,
    repo_name: str,
    description: str | None = None,
) -> str:
    """Initialize a git repository from `directory`, create a GitHub repo and push.

    Args:
        directory: Path to the directory containing the data to publish.
        repo_name: Name of the GitHub repository to create.
        private: Whether the GitHub repo should be private.
        description: Optional description for the GitHub repo.
        "main": Default "main" name.
        "first commit": Message for the initial commit.
        owner: Optional org/user to create the repo under. Defaults to the
            authenticated user.
        remote: Name of the git remote to add.

    Returns:
        The HTTPS URL of the created GitHub repository.
    """
    path = Path(directory).expanduser().resolve()
    if not path.is_dir():
        raise GitRepoError(f"Directory does not exist or is not a directory: {path}")

    _ensure_gh_authenticated()

    if not (path / ".git").exists():
        _run(["git", "init", "-b", "main"], cwd=path)
    else:
        _run(["git", "checkout", "-B", "main"], cwd=path)

    _run(["git", "add", "-A"], cwd=path)

    status = _run(["git", "status", "--porcelain"], cwd=path)
    if status.stdout.strip():
        _run(["git", "commit", "-m", "first commit"], cwd=path)

    full_name = f"{owner}/{repo_name}"
    create_cmd = ["gh", "repo", "create", full_name, "--public", "--source", str(path)]
    if description:
        create_cmd.extend(["--description", description])

    existing = subprocess.run(
        ["gh", "repo", "view", full_name, "--json", "url"],
        cwd=str(path),
        text=True,
        capture_output=True,
    )
    if existing.returncode == 0:
        url = json.loads(existing.stdout)["url"]
        remotes = _run(["git", "remote"], cwd=path).stdout.split()
        if "origin" not in remotes:
            _run(["git", "remote", "add", "origin", f"{url}.git"], cwd=path)
    else:
        _run(create_cmd, cwd=path)
        view = _run(["gh", "repo", "view", full_name, "--json", "url"], cwd=path)
        url = json.loads(view.stdout)["url"]

    _run(["git", "push", "-u", "origin", "main"], cwd=path)
    return url
