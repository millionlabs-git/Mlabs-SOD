"""Post-deploy E2E tester — runs USER_FLOWS.md against the live app with Visual Playwright."""
from __future__ import annotations

import re
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.testing import e2e_tester_prompt
from src.prompts.system import load_skill
from src.pipeline.agent import run_agent


def parse_test_report(repo_path: str) -> dict:
    """Parse docs/TEST_REPORT.md and return structured results.

    Returns:
        {
            "total": int,
            "passed": int,
            "failed": int,
            "blocked": int,
            "failed_flows": ["flow-id", ...],
            "blocked_flows": ["flow-id", ...],
            "all_passed": bool,
            "raw": str,
        }
    """
    report_path = Path(repo_path) / "docs" / "TEST_REPORT.md"
    if not report_path.exists():
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [],
            "all_passed": False, "raw": "",
        }

    raw = report_path.read_text()

    # Parse summary numbers
    total = _extract_int(raw, r"total_flows:\s*(\d+)")
    passed = _extract_int(raw, r"passed:\s*(\d+)")
    failed = _extract_int(raw, r"failed:\s*(\d+)")
    blocked = _extract_int(raw, r"blocked:\s*(\d+)")

    # Parse individual flow results
    failed_flows = re.findall(r"###\s+FAIL:\s+([\w-]+)", raw)
    blocked_flows = re.findall(r"###\s+BLOCKED:\s+([\w-]+)", raw)

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "blocked": blocked,
        "failed_flows": failed_flows,
        "blocked_flows": blocked_flows,
        "all_passed": failed == 0 and blocked == 0 and total > 0,
        "raw": raw,
    }


def _extract_int(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


async def run_e2e_tests(
    repo_path: str,
    app_url: str,
    config: Config,
    reporter: StatusReporter,
    retest_only: list[str] | None = None,
) -> dict:
    """Run E2E tests against the live app.

    Args:
        repo_path: Path to the cloned repo.
        app_url: Live deployed URL.
        config: Worker config.
        reporter: Status reporter.
        retest_only: If set, only test these flow IDs (for retest iterations).

    Returns:
        Parsed test report dict from parse_test_report().
    """
    iteration_label = "retest" if retest_only else "full"
    await reporter.report("e2e_testing_started", {"mode": iteration_label})
    print(f"[tester] Starting E2E tests ({iteration_label})...")

    # Read flow and seed specs
    user_flows_path = Path(repo_path) / "docs" / "USER_FLOWS.md"
    seed_data_path = Path(repo_path) / "docs" / "SEED_DATA.md"

    if not user_flows_path.exists():
        print("[tester] No USER_FLOWS.md found — skipping E2E tests")
        await reporter.report("e2e_testing_skipped", {"reason": "no USER_FLOWS.md"})
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [],
            "all_passed": True, "raw": "",
        }

    user_flows_content = user_flows_path.read_text()
    seed_data_content = seed_data_path.read_text() if seed_data_path.exists() else ""

    screenshots_dir = f"{repo_path}/docs/screenshots/e2e"
    Path(screenshots_dir).mkdir(parents=True, exist_ok=True)

    vp_system = load_skill("visual-playwright")

    prompt = e2e_tester_prompt(
        user_flows_content=user_flows_content,
        seed_data_content=seed_data_content,
        app_url=app_url,
        vp_script=config.vp_script_path,
        resend_api_key=config.resend_api_key,
        screenshots_dir=screenshots_dir,
        retest_only=retest_only,
    )

    try:
        result = await run_agent(
            prompt=prompt,
            system_prompt=vp_system,
            allowed_tools=["Bash", "Read", "Write", "Grep", "Glob"],
            cwd=repo_path,
            model="claude-sonnet-4-6",
            max_turns=50,
            reporter=reporter,
        )

        report = parse_test_report(repo_path)

        await reporter.report("e2e_testing_complete", {
            "total": report["total"],
            "passed": report["passed"],
            "failed": report["failed"],
            "blocked": report["blocked"],
            "all_passed": report["all_passed"],
            "cost_usd": result.cost_usd,
        })
        print(
            f"[tester] E2E complete — "
            f"{report['passed']}/{report['total']} passed, "
            f"{report['failed']} failed, {report['blocked']} blocked"
        )
        return report

    except Exception as e:
        print(f"[tester] E2E testing error: {e}")
        await reporter.report("e2e_testing_failed", {"error": str(e)[:500]})
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [],
            "all_passed": False, "raw": f"Error: {e}",
        }
