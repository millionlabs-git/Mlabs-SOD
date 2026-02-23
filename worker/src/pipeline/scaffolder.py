"""Phase 2: Generate the project skeleton."""
from __future__ import annotations

from src.config import Config
from src.status import StatusReporter
from src.prompts.system import load_skills
from src.prompts.implementation import scaffold_prompt
from src.pipeline.agent import run_agent
from src.repo import git_commit, git_push


async def scaffold_project(
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str | None = None,
) -> None:
    """Create directory structure, configs, deps, test infra, CI."""
    await reporter.report("scaffolding_started")

    system = load_skills(["coding-standards", "backend-patterns"])

    await run_agent(
        prompt=scaffold_prompt(),
        system_prompt=system,
        allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        cwd=repo_path,
        model=config.model,
    )

    await reporter.report("scaffold_complete")
    await reporter.report("dependencies_installed")

    # Commit and push scaffold for resumability
    if branch_name:
        git_commit(repo_path, "chore: scaffold project structure")
        git_push(repo_path, branch_name)
        print("[scaffolder] Pushed scaffold artifacts")

    print("[scaffolder] Project scaffolding complete")
