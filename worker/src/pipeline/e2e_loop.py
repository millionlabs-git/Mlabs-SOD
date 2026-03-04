"""Fix-retest loop — runs E2E tests, fixes failures, redeploys, retests."""
from __future__ import annotations

import time
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.testing import e2e_fix_prompt
from src.pipeline.agent import run_agent
from src.pipeline.tester import run_e2e_tests, parse_test_report
from src.pipeline.deployer import deploy_checkpoint
from src.repo import git_commit, git_push


async def run_e2e_loop(
    repo_path: str,
    app_url: str,
    fly_app_name: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str | None = None,
    timeout_hours: float = 5,
    cost_limit_usd: float = 150.0,
) -> dict:
    """Run the fix-retest loop until all E2E tests pass, or time/cost limit hit.

    Flow:
    1. Run E2E tests
    2. If all pass → done
    3. If failures → fixer fixes all → redeploy → retest (only failed + smoke)
    4. Repeat until all pass, timeout_hours exceeded, or cost_limit_usd exceeded

    Returns the final test report dict.
    """
    deadline = time.monotonic() + timeout_hours * 3600
    total_cost = 0.0
    await reporter.report("e2e_loop_started", {
        "timeout_hours": timeout_hours,
        "cost_limit_usd": cost_limit_usd,
    })
    print(f"[e2e-loop] Starting fix-retest loop (limit: {timeout_hours}h / ${cost_limit_usd})")

    # First run: test everything
    report = await run_e2e_tests(
        repo_path=repo_path,
        app_url=app_url,
        config=config,
        reporter=reporter,
    )

    if report["all_passed"]:
        print("[e2e-loop] All tests passed on first run!")
        await reporter.report("e2e_loop_complete", {
            "iterations": 1,
            "result": "all_passed",
            "cost_usd": total_cost,
            **_report_summary(report),
        })
        return report

    # Fix-retest loop
    iteration = 1
    while True:
        iteration += 1
        remaining_mins = int((deadline - time.monotonic()) / 60)
        if remaining_mins <= 0:
            print(f"[e2e-loop] Time limit reached after {iteration - 1} iterations.")
            break
        if total_cost >= cost_limit_usd:
            print(f"[e2e-loop] Cost limit reached (${total_cost:.2f}) after {iteration - 1} iterations.")
            break

        print(
            f"[e2e-loop] Iteration {iteration} "
            f"(~{remaining_mins}m left, ${total_cost:.2f}/${cost_limit_usd} spent) "
            f"— fixing {report['failed']} failures..."
        )
        await reporter.report("e2e_fix_started", {
            "iteration": iteration,
            "failed_flows": report["failed_flows"],
            "cost_usd": total_cost,
        })

        # Run fixer agent on all failures
        try:
            result = await run_agent(
                prompt=e2e_fix_prompt(report["raw"], iteration),
                allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
                cwd=repo_path,
                model=config.model,
                max_turns=30,
                reporter=reporter,
                agent_label=f"e2e-fixer-{iteration}",
            )
            total_cost += result.cost_usd
        except Exception as e:
            print(f"[e2e-loop] Fixer failed: {e}")
            await reporter.report("e2e_fix_failed", {"iteration": iteration, "error": str(e)[:500]})
            continue

        # Commit fixes
        git_commit(repo_path, f"fix: resolve E2E test failures (iteration {iteration})")
        if branch_name:
            git_push(repo_path, branch_name)

        # Redeploy
        print(f"[e2e-loop] Redeploying to {fly_app_name}...")
        await reporter.report("e2e_redeploy_started", {"iteration": iteration})

        checkpoint = deploy_checkpoint(
            repo_path=repo_path,
            app_name=fly_app_name,
            checkpoint_name=f"e2e-fix-{iteration}",
        )

        if not checkpoint.passed:
            print(f"[e2e-loop] Redeploy failed: {checkpoint.issues}")
            await reporter.report("e2e_redeploy_failed", {
                "iteration": iteration,
                "issues": checkpoint.issues,
            })
            continue

        await reporter.report("e2e_redeploy_complete", {"iteration": iteration})

        # Retest — only failed + blocked flows (plus smoke)
        retest_flows = report["failed_flows"] + report["blocked_flows"]
        report = await run_e2e_tests(
            repo_path=repo_path,
            app_url=app_url,
            config=config,
            reporter=reporter,
            retest_only=retest_flows,
        )

        if report["all_passed"]:
            print(f"[e2e-loop] All tests passed after {iteration} iterations! (${total_cost:.2f} spent)")
            await reporter.report("e2e_loop_complete", {
                "iterations": iteration,
                "result": "all_passed",
                "cost_usd": total_cost,
                **_report_summary(report),
            })
            return report

    # Limit exhausted
    reason = "timeout" if remaining_mins <= 0 else "cost_limit"
    print(f"[e2e-loop] Stopped ({reason}) after {iteration - 1} iterations. {report['failed']} flows still failing.")
    await reporter.report("e2e_loop_complete", {
        "iterations": iteration - 1,
        "result": reason,
        "cost_usd": total_cost,
        **_report_summary(report),
    })
    return report


def _report_summary(report: dict) -> dict:
    return {
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "blocked": report["blocked"],
    }
