"""Worker entrypoint — bootstrap, run the full build pipeline, exit."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.repo import clone_repo, create_branch, setup_github_auth
from src.prd_parser import parse_prd
from src.prompts.system import set_config_path, set_vp_skill_path
from src.pipeline.planner import plan_build
from src.pipeline.scaffolder import scaffold_project
from src.pipeline.builder import build_tasks
from src.pipeline.reviewer import review_build
from src.pipeline.finalizer import finalize


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

        # 1. Setup GitHub auth
        setup_github_auth(config.github_token)

        # 2. Clone repo
        repo_path = clone_repo(
            config.repo_url, config.branch, config.workspace_path
        )
        await reporter.report("repo_cloned")
        print(f"[main] Cloned to {repo_path}")

        # 3. Create build branch
        branch_name = f"auto-build/{config.job_id[:8]}"
        create_branch(repo_path, branch_name)

        # 4. Parse PRD
        prd_content = parse_prd(repo_path, config.prd_path)
        await reporter.report("prd_parsed")
        print(f"[main] PRD loaded ({len(prd_content)} chars)")

        # 5. Plan
        plan = await plan_build(prd_content, repo_path, config, reporter)

        # 6. Scaffold
        await scaffold_project(repo_path, config, reporter)

        # 7. Build tasks
        await build_tasks(plan, repo_path, config, reporter)

        # 8. Review
        await review_build(repo_path, config, reporter)

        # 9. Finalize (push + PR)
        await finalize(repo_path, config, reporter)

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
