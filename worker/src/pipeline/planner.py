"""Phase 1: Plan the build — architecture + task decomposition."""
from __future__ import annotations

from src.config import Config
from src.status import StatusReporter
from src.prompts.system import load_agent
from src.prompts.planning import architecture_prompt, task_decomposition_prompt
from src.pipeline.agent import run_agent
from src.pipeline.models import BuildPlan, parse_build_plan
from src.repo import git_commit, git_push


async def plan_build(
    prd_content: str,
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str | None = None,
) -> BuildPlan:
    """Turn a PRD into an architecture doc and ordered task list.

    Two-pass approach:
    1. Architect agent designs the system
    2. Planner agent decomposes into tasks
    """
    await reporter.report("planning_started")

    # Pass 1: Architecture design
    print("[planner] Running architect agent...")
    architect_system = load_agent("architect.md")

    await run_agent(
        prompt=architecture_prompt(prd_content),
        system_prompt=architect_system,
        allowed_tools=["Read", "Write", "Bash", "WebSearch"],
        cwd=repo_path,
        model=config.model,
    )
    await reporter.report("architecture_designed")

    # Pass 2: Task decomposition
    print("[planner] Running planner agent...")
    planner_system = load_agent("planner.md")

    await run_agent(
        prompt=task_decomposition_prompt(),
        system_prompt=planner_system,
        allowed_tools=["Read", "Write"],
        cwd=repo_path,
        model=config.model,
    )

    # Parse the generated plan
    plan = parse_build_plan(f"{repo_path}/docs/BUILD_PLAN.md")
    await reporter.report("tasks_identified", {
        "count": plan.total_tasks,
        "ui_tasks": plan.ui_task_count,
    })

    print(f"[planner] Plan: {plan.total_tasks} tasks ({plan.ui_task_count} with UI)")

    # Commit and push planning artifacts for resumability
    if branch_name:
        git_commit(repo_path, "docs: add architecture and build plan")
        git_push(repo_path, branch_name)
        print("[planner] Pushed planning artifacts")

    return plan
