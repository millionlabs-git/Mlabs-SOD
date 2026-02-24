"""Orchestrator pipeline runner — manages phases with context threading and evaluation."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.orchestrator.progress import ProgressTracker
from src.orchestrator.tech_detector import detect_tech_stack
from src.orchestrator.component_loader import ComponentLoader
from src.orchestrator.context import ContextBuilder
from src.orchestrator.evaluator import evaluate_phase
from src.pipeline.models import parse_build_plan
from src.repo import git_commit, git_push


def _commit_progress(repo_path: str, branch_name: str | None) -> None:
    """Commit PROGRESS.json and optionally push."""
    git_commit(repo_path, "chore: update pipeline progress")


async def run_pipeline(
    prd_content: str,
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str,
    skip: dict[str, bool],
) -> dict | None:
    """Run the full build pipeline with context threading and evaluation.

    Phases:
      1. Planning   — architecture design + task decomposition
      2. Scaffolding — project skeleton generation
      3. Building    — iterative task implementation
      4. Review      — code review, security review, visual E2E
      5. Finalize    — push branch, create PR
      6. Deploy      — Neon DB + Fly.io deployment

    Each phase records progress in docs/PROGRESS.json so the pipeline can
    resume on re-trigger.  Evaluations run after key phases to score output
    quality but never block the pipeline.
    """
    # ── Initialize orchestrator components ────────────────────────────────
    progress = ProgressTracker(repo_path, config.job_id)
    tech_profile = detect_tech_stack(repo_path)

    print(
        f"[runner] Tech profile: {tech_profile.languages}, "
        f"frontend={tech_profile.frontend_framework}, "
        f"backend={tech_profile.backend_framework}, "
        f"db={tech_profile.database}"
    )

    progress.update_tech_profile(asdict(tech_profile))

    loader = ComponentLoader(
        config.claude_config_path, config.vp_script_path, tech_profile
    )
    context_builder = ContextBuilder(repo_path)

    plan = None
    plan_path = f"{repo_path}/docs/BUILD_PLAN.md"

    # ── Phase 1: Planning ─────────────────────────────────────────────────
    if skip.get("planning"):
        print("[runner] Skipping planning (already complete)")
        progress.skip_phase("planning")
        if Path(plan_path).exists():
            plan = parse_build_plan(plan_path)
    else:
        progress.start_phase("planning")
        try:
            from src.pipeline.planner import plan_build

            plan = await plan_build(
                prd_content, repo_path, config, reporter, branch_name
            )

            # Re-detect tech stack now that architecture doc exists
            tech_profile = detect_tech_stack(repo_path)
            loader = ComponentLoader(
                config.claude_config_path, config.vp_script_path, tech_profile
            )
            progress.update_tech_profile(asdict(tech_profile))

            # Evaluate architecture quality
            eval_result = await evaluate_phase(
                "architecture", repo_path, config, context_builder
            )
            progress.record_evaluation("planning", eval_result.score)
            if not eval_result.passed:
                print(
                    f"[runner] Architecture evaluation failed: {eval_result.issues}"
                )

            progress.complete_phase("planning")
            progress.save()
            _commit_progress(repo_path, branch_name)
        except Exception as exc:
            progress.fail_phase("planning", str(exc))
            progress.save()
            raise

    # ── Phase 2: Scaffolding ──────────────────────────────────────────────
    if skip.get("scaffolding"):
        print("[runner] Skipping scaffolding (already complete)")
        progress.skip_phase("scaffolding")
    else:
        progress.start_phase("scaffolding")
        try:
            from src.pipeline.scaffolder import scaffold_project

            await scaffold_project(repo_path, config, reporter, branch_name)

            # Evaluate scaffold output
            eval_result = await evaluate_phase(
                "scaffolding", repo_path, config, context_builder
            )
            progress.record_evaluation("scaffolding", eval_result.score)
            if not eval_result.passed:
                print(
                    f"[runner] Scaffold evaluation failed: {eval_result.issues}"
                )

            progress.complete_phase("scaffolding")
            progress.save()
            _commit_progress(repo_path, branch_name)
        except Exception as exc:
            progress.fail_phase("scaffolding", str(exc))
            progress.save()
            raise

    # ── Phase 3: Building ─────────────────────────────────────────────────
    if skip.get("building"):
        print("[runner] Skipping building (all tasks already complete)")
        progress.skip_phase("building")
    elif plan:
        progress.start_phase("building")
        try:
            from src.pipeline.builder import build_tasks

            await build_tasks(plan, repo_path, config, reporter, branch_name)

            progress.complete_phase("building")
            progress.save()
            _commit_progress(repo_path, branch_name)
        except Exception as exc:
            progress.fail_phase("building", str(exc))
            progress.save()
            raise
    else:
        print("[runner] Warning: no build plan available, skipping build tasks")
        progress.skip_phase("building")

    # ── Phase 4: Review ───────────────────────────────────────────────────
    if skip.get("review"):
        print("[runner] Skipping review (already complete)")
        progress.skip_phase("review")
    else:
        progress.start_phase("review")
        try:
            from src.pipeline.reviewer import review_build

            await review_build(repo_path, config, reporter, branch_name)

            progress.complete_phase("review")
            progress.save()
            _commit_progress(repo_path, branch_name)
        except Exception as exc:
            progress.fail_phase("review", str(exc))
            progress.save()
            raise

    # ── Phase 5: Finalize ─────────────────────────────────────────────────
    if config.mode != "deploy-only":
        progress.start_phase("finalize")
        try:
            from src.pipeline.finalizer import finalize

            await finalize(repo_path, config, reporter, branch_name)

            progress.complete_phase("finalize")
            progress.save()
            _commit_progress(repo_path, branch_name)
        except Exception as exc:
            progress.fail_phase("finalize", str(exc))
            progress.save()
            raise
    else:
        print("[runner] Skipping finalize (deploy-only mode)")
        progress.skip_phase("finalize")

    # ── Phase 6: Deploy ───────────────────────────────────────────────────
    deploy_result: dict | None = None

    if config.fly_api_token and not skip.get("deployment"):
        progress.start_phase("deployment")
        try:
            from src.pipeline.deployer import deploy

            deploy_result = await deploy(
                repo_path, config, reporter, branch_name
            )

            progress.complete_phase("deployment")
            progress.save()
            _commit_progress(repo_path, branch_name)
        except Exception as exc:
            progress.fail_phase("deployment", str(exc))
            progress.save()
            raise
    elif skip.get("deployment"):
        print("[runner] Skipping deployment (already complete)")
        progress.skip_phase("deployment")

    return deploy_result
