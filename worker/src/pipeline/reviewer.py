"""Phase 4: Code review, security review, and visual E2E sweep."""
from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.system import load_agent, load_skill
from src.prompts.review import code_review_prompt, security_review_prompt, visual_e2e_prompt
from src.pipeline.agent import run_agent
from src.repo import git_commit, git_push


async def review_build(
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str | None = None,
) -> None:
    """Run code review, security review, and full visual E2E sweep."""
    await reporter.report("review_started")

    # Code quality review
    print("[reviewer] Running code review agent...")
    await run_agent(
        prompt=code_review_prompt(),
        system_prompt=load_agent("code-reviewer.md"),
        allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        cwd=repo_path,
        model=config.model,
    )

    # Security review
    print("[reviewer] Running security review agent...")
    await run_agent(
        prompt=security_review_prompt(),
        system_prompt=load_agent("security-reviewer.md"),
        allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        cwd=repo_path,
        model=config.model,
    )

    await reporter.report("review_complete")

    # Visual E2E sweep
    await _visual_e2e_sweep(repo_path, config, reporter)

    # Commit and push review artifacts for resumability
    if branch_name:
        git_commit(repo_path, "docs: add review results")
        git_push(repo_path, branch_name)
        print("[reviewer] Pushed review artifacts")


async def _visual_e2e_sweep(
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
) -> None:
    """Navigate every route, screenshot each, verify the whole app."""
    e2e_dir = f"{repo_path}/docs/screenshots/e2e"
    Path(e2e_dir).mkdir(parents=True, exist_ok=True)

    await reporter.report("visual_e2e_started")

    vp_system = load_skill("visual-playwright")
    e2e_agent = load_agent("e2e-runner.md")
    combined_system = f"{vp_system}\n\n---\n\n{e2e_agent}" if e2e_agent else vp_system

    try:
        await run_agent(
            prompt=visual_e2e_prompt(config.vp_script_path, e2e_dir),
            system_prompt=combined_system,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            cwd=repo_path,
            model=config.model,
        )
    except Exception as e:
        print(f"[reviewer] Visual E2E sweep error (non-fatal): {e}")
        await reporter.report("visual_e2e_failed", {"error": str(e)})
        return

    screenshot_count = len(list(Path(e2e_dir).glob("*.png")))
    await reporter.report("visual_e2e_complete", {
        "screenshots": screenshot_count,
    })
    print(f"[reviewer] Visual E2E complete — {screenshot_count} screenshots captured")
