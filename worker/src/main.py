"""Worker entrypoint — bootstrap, run the full build pipeline, exit."""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import sys
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.repo import (
    clone_repo,
    create_branch,
    setup_github_auth,
    branch_exists_remote,
    checkout_existing_branch,
)
from src.github_auth import get_installation_token
from src.prd_parser import parse_prd
from src.prompts.system import set_config_path, set_vp_skill_path
from src.pipeline.planner import plan_build
from src.pipeline.scaffolder import scaffold_project
from src.pipeline.builder import build_tasks
from src.pipeline.reviewer import review_build
from src.pipeline.finalizer import finalize
from src.pipeline.models import parse_build_plan


def _detect_completed_phases(repo_path: str, plan_path: str) -> dict[str, bool]:
    """Check which pipeline phases have already been completed on this branch."""
    phases: dict[str, bool] = {
        "planning": False,
        "scaffolding": False,
        "building": False,
        "review": False,
        "deployment": False,
    }

    # Planning is done if BUILD_PLAN.md exists
    if Path(plan_path).exists():
        phases["planning"] = True

    # Scaffolding is done if there are source files beyond docs/
    repo = Path(repo_path)
    has_source = any(
        p.is_file()
        for p in repo.iterdir()
        if p.name not in (".git", "docs", ".gitignore", "README.md", ".github")
    ) or (repo / "package.json").exists() or (repo / "pyproject.toml").exists()
    if has_source and phases["planning"]:
        phases["scaffolding"] = True

    # Building is done if the committed task count matches the plan
    if phases["scaffolding"] and Path(plan_path).exists():
        try:
            plan = parse_build_plan(plan_path)
            result = subprocess.run(
                ["git", "log", "--oneline", "--grep=^feat:"],
                cwd=repo_path,
                capture_output=True,
                text=True,
            )
            feat_commits = [
                line for line in result.stdout.strip().splitlines() if line
            ]
            if len(feat_commits) >= plan.total_tasks:
                phases["building"] = True
        except Exception:
            pass

    # Review is done if review docs exist
    review_files = list((repo / "docs").glob("*REVIEW*")) if (repo / "docs").exists() else []
    if phases["building"] and review_files:
        phases["review"] = True

    # Deployment is done if DEPLOYMENT.md exists
    deploy_info = repo / "docs" / "DEPLOYMENT.md"
    if phases["review"] and deploy_info.exists():
        phases["deployment"] = True

    return phases


async def main() -> None:
    config = Config.from_env()
    reporter = StatusReporter(
        config.orchestrator_url, config.job_id, config.webhook_secret
    )

    # Configure prompt loaders with actual paths
    set_config_path(config.claude_config_path)
    set_vp_skill_path(f"{config.vp_script_path.rsplit('/scripts', 1)[0]}/SKILL.md")

    try:
        await reporter.report("worker_started")
        print(f"[main] Worker started for job {config.job_id}")
        print(f"[main] Repo: {config.repo_url}@{config.branch}")
        print(f"[main] PRD: {config.prd_path}")

        # 1. Setup GitHub auth (generate installation token from App credentials)
        print("[main] Generating GitHub App installation token...")
        github_token = await get_installation_token(
            config.github_app_id,
            config.github_app_installation_id,
            config.github_app_private_key,
        )
        setup_github_auth(github_token)

        # 2. Clone repo
        repo_path = clone_repo(
            config.repo_url, config.branch, config.workspace_path
        )
        await reporter.report("repo_cloned")
        print(f"[main] Cloned to {repo_path}")

        # 3. Create or resume build branch
        # Use a deterministic branch name based on repo+prd so re-triggers
        # find the same branch and can resume where the previous run left off.
        branch_hash = hashlib.sha256(
            f"{config.repo_url}:{config.prd_path}".encode()
        ).hexdigest()[:8]
        branch_name = f"auto-build/{branch_hash}"
        resuming = False

        if branch_exists_remote(repo_path, branch_name):
            print(f"[main] Branch {branch_name} exists remotely — resuming")
            checkout_existing_branch(repo_path, branch_name)
            resuming = True
        else:
            create_branch(repo_path, branch_name)

        # 4. Parse PRD
        prd_content = parse_prd(repo_path, config.prd_path)
        await reporter.report("prd_parsed")
        print(f"[main] PRD loaded ({len(prd_content)} chars)")

        # 5. Detect completed phases if resuming
        plan_path = f"{repo_path}/docs/BUILD_PLAN.md"
        skip = _detect_completed_phases(repo_path, plan_path) if resuming else {}

        if skip:
            skipped = [phase for phase, done in skip.items() if done]
            if skipped:
                print(f"[main] Resuming — skipping completed phases: {', '.join(skipped)}")

        # 6. Plan
        if skip.get("planning"):
            print("[main] Skipping planning (already complete)")
            plan = parse_build_plan(plan_path)
        else:
            plan = await plan_build(prd_content, repo_path, config, reporter, branch_name)

        # 7. Scaffold
        if skip.get("scaffolding"):
            print("[main] Skipping scaffolding (already complete)")
        else:
            await scaffold_project(repo_path, config, reporter, branch_name)

        # 8. Build tasks
        if skip.get("building"):
            print("[main] Skipping building (all tasks already complete)")
        else:
            await build_tasks(plan, repo_path, config, reporter, branch_name)

        # 9. Review
        if skip.get("review"):
            print("[main] Skipping review (already complete)")
        else:
            await review_build(repo_path, config, reporter, branch_name)

        # 10. Finalize (push + PR)
        await finalize(repo_path, config, reporter, branch_name)

        # 11. Deploy (Neon DB + Netlify)
        if config.netlify_auth_token and not skip.get("deployment"):
            from src.pipeline.deployer import deploy
            deploy_result = await deploy(repo_path, config, reporter, branch_name)
            print(f"[main] Deploy result: {deploy_result}")
        elif skip.get("deployment"):
            print("[main] Skipping deployment (already complete)")

        print("[main] Build completed successfully")

    except Exception as e:
        print(f"[main] Build failed: {e}")
        await reporter.report("build_failed", {"reason": str(e)})
        sys.exit(1)


def run() -> None:
    """Sync entrypoint for the worker."""
    asyncio.run(main())


if __name__ == "__main__":
    run()
