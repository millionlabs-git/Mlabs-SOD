"""Worker entrypoint — bootstrap, run the full build pipeline, exit."""
from __future__ import annotations

import asyncio
import hashlib
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
from src.orchestrator.runner import run_pipeline
from src.orchestrator.progress import ProgressTracker


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
        # In deploy-only mode, stay on the current branch (no build branch needed)
        branch_name = config.branch
        resuming = False

        if config.mode != "deploy-only":
            # Use a deterministic branch name based on repo+prd so re-triggers
            # find the same branch and can resume where the previous run left off.
            branch_hash = hashlib.sha256(
                f"{config.repo_url}:{config.prd_path}".encode()
            ).hexdigest()[:8]
            branch_name = f"auto-build/{branch_hash}"

            if branch_exists_remote(repo_path, branch_name):
                print(f"[main] Branch {branch_name} exists remotely — resuming")
                checkout_existing_branch(repo_path, branch_name)
                resuming = True
            else:
                create_branch(repo_path, branch_name)
        else:
            print(f"[main] Deploy-only mode — staying on {branch_name}")

        # 4. Parse PRD
        prd_content = parse_prd(repo_path, config.prd_path)
        await reporter.report("prd_parsed")
        print(f"[main] PRD loaded ({len(prd_content)} chars)")

        # 5. Detect completed phases based on mode
        if config.mode == "deploy-only":
            print("[main] Mode: deploy-only — skipping all build phases")
            skip = {
                "planning": True,
                "scaffolding": True,
                "building": True,
                "review": True,
                "deployment": False,
            }
        elif config.mode == "auto":
            print("[main] Mode: auto — running maturity assessment...")
            from src.pipeline.assessor import assess_maturity
            skip = await assess_maturity(repo_path, prd_content, config, reporter)
        elif resuming:
            # Use PROGRESS.json for resumability when available
            progress = ProgressTracker(repo_path, config.job_id)
            if progress.progress.phases:  # Has saved progress
                skip = progress.get_skip_map()
                print(f"[main] Resuming from PROGRESS.json: {skip}")
            else:
                skip = {}
        else:
            skip = {}

        if skip:
            skipped = [phase for phase, done in skip.items() if done]
            if skipped:
                print(f"[main] Skipping completed phases: {', '.join(skipped)}")

        # 6. Run the pipeline
        deploy_result = await run_pipeline(
            prd_content=prd_content,
            repo_path=repo_path,
            config=config,
            reporter=reporter,
            branch_name=branch_name,
            skip=skip,
        )

        if deploy_result:
            print(f"[main] Deploy result: {deploy_result}")

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
