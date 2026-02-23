from __future__ import annotations

import subprocess
from pathlib import Path


def run(cmd: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a shell command, raising on failure."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr}"
        )
    return result


def setup_github_auth(token: str) -> None:
    """Configure git and gh CLI with the GitHub token."""
    run(["git", "config", "--global", "credential.helper", "store"])
    # Write credentials for HTTPS clone/push
    cred_path = Path.home() / ".git-credentials"
    cred_path.write_text(f"https://x-access-token:{token}@github.com\n")
    cred_path.chmod(0o600)
    # Also set for gh CLI
    subprocess.run(
        ["gh", "auth", "login", "--with-token"],
        input=token,
        capture_output=True,
        text=True,
    )


def clone_repo(repo_url: str, branch: str, dest: str) -> str:
    """Clone a repo to dest directory. Returns the repo path."""
    repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")
    repo_path = str(Path(dest) / repo_name)
    run(["git", "clone", "--depth", "1", "--branch", branch, repo_url, repo_path])
    # Unshallow so we can push new branches
    run(["git", "fetch", "--unshallow"], cwd=repo_path)
    return repo_path


def create_branch(repo_path: str, branch_name: str) -> None:
    """Create and checkout a new branch."""
    run(["git", "checkout", "-b", branch_name], cwd=repo_path)


def git_commit(repo_path: str, message: str) -> None:
    """Stage all changes and commit."""
    run(["git", "add", "-A"], cwd=repo_path)
    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_path,
        capture_output=True,
    )
    if result.returncode != 0:
        run(["git", "commit", "-m", message], cwd=repo_path)


def git_push(repo_path: str, branch_name: str) -> None:
    """Push branch to origin."""
    run(["git", "push", "-u", "origin", branch_name], cwd=repo_path)


def create_pr(
    repo_path: str,
    branch_name: str,
    title: str,
    body_file: str,
    base: str = "main",
) -> str:
    """Create a GitHub PR and return its URL."""
    result = run(
        [
            "gh", "pr", "create",
            "--title", title,
            "--body-file", body_file,
            "--base", base,
            "--head", branch_name,
        ],
        cwd=repo_path,
    )
    return result.stdout.strip()
