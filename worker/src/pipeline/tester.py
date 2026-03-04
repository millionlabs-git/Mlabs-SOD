"""Post-deploy E2E tester — runs USER_FLOWS.md against the live app with Visual Playwright.

Splits flows into batches of 6 to avoid context overflow. Each batch
gets its own agent run with max_turns=25 (instead of 50 for all flows).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.testing import e2e_batch_tester_prompt
from src.prompts.system import load_skill
from src.pipeline.agent import run_agent


# ---------------------------------------------------------------------------
# FlowSpec + parsing
# ---------------------------------------------------------------------------

@dataclass
class FlowSpec:
    """A single user flow parsed from USER_FLOWS.md."""
    flow_id: str
    priority: str = "medium"
    user_type: str = ""
    depends_on: list[str] = field(default_factory=list)
    raw_text: str = ""


def parse_user_flows(content: str) -> list[FlowSpec]:
    """Split USER_FLOWS.md on ``## Flow:`` headers into FlowSpec objects."""
    flows: list[FlowSpec] = []
    # Split on ## Flow: headers, keeping the header with each chunk
    chunks = re.split(r"(?=^## Flow:\s*)", content, flags=re.MULTILINE)

    for chunk in chunks:
        header_match = re.match(r"^## Flow:\s*([\w-]+)", chunk)
        if not header_match:
            continue

        flow_id = header_match.group(1)
        priority = "medium"
        user_type = ""
        depends_on: list[str] = []

        pm = re.search(r"priority:\s*(critical|high|medium|low)", chunk, re.IGNORECASE)
        if pm:
            priority = pm.group(1).lower()

        um = re.search(r"user_type:\s*(\S+)", chunk)
        if um:
            user_type = um.group(1)

        dm = re.search(r"depends_on:\s*\[([^\]]*)\]", chunk)
        if dm:
            deps_raw = dm.group(1).strip()
            if deps_raw:
                depends_on = [d.strip().strip("'\"") for d in deps_raw.split(",") if d.strip()]

        flows.append(FlowSpec(
            flow_id=flow_id,
            priority=priority,
            user_type=user_type,
            depends_on=depends_on,
            raw_text=chunk.strip(),
        ))

    return flows


# ---------------------------------------------------------------------------
# Batching (topological sort + chunking)
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# How many critical smoke flows to add when retesting
_SMOKE_COUNT = 3


def batch_flows(
    flows: list[FlowSpec],
    batch_size: int = 6,
    retest_only: list[str] | None = None,
) -> list[list[FlowSpec]]:
    """Topological-sort flows by dependencies and chunk into batches.

    When *retest_only* is set, include only those flows plus up to
    ``_SMOKE_COUNT`` critical smoke flows for regression coverage.
    """
    by_id = {f.flow_id: f for f in flows}

    if retest_only:
        retest_set = set(retest_only)
        # Add critical smoke flows
        critical = [f for f in flows if f.priority == "critical" and f.flow_id not in retest_set]
        for f in critical[:_SMOKE_COUNT]:
            retest_set.add(f.flow_id)
        flows = [f for f in flows if f.flow_id in retest_set]

    # Topological sort via Kahn's algorithm
    in_degree: dict[str, int] = {f.flow_id: 0 for f in flows}
    flow_ids = set(in_degree.keys())
    for f in flows:
        for dep in f.depends_on:
            if dep in flow_ids:
                in_degree[f.flow_id] += 1

    queue: list[FlowSpec] = []
    for f in flows:
        if in_degree[f.flow_id] == 0:
            queue.append(f)
    # Sort initial queue by priority
    queue.sort(key=lambda f: _PRIORITY_ORDER.get(f.priority, 2))

    ordered: list[FlowSpec] = []
    while queue:
        current = queue.pop(0)
        ordered.append(current)
        for f in flows:
            if current.flow_id in f.depends_on and f.flow_id in flow_ids:
                in_degree[f.flow_id] -= 1
                if in_degree[f.flow_id] == 0:
                    queue.append(f)
        queue.sort(key=lambda f: _PRIORITY_ORDER.get(f.priority, 2))

    # If there are remaining flows (cycles), just append them
    ordered_ids = {f.flow_id for f in ordered}
    for f in flows:
        if f.flow_id not in ordered_ids:
            ordered.append(f)

    # Chunk into batches
    batches: list[list[FlowSpec]] = []
    for i in range(0, len(ordered), batch_size):
        batches.append(ordered[i : i + batch_size])

    return batches


# ---------------------------------------------------------------------------
# Batch report parsing + aggregation
# ---------------------------------------------------------------------------

def _parse_batch_report(repo_path: str, batch_idx: int) -> dict:
    """Parse a single batch report file."""
    report_path = Path(repo_path) / "docs" / f"TEST_REPORT_BATCH_{batch_idx}.md"
    if not report_path.exists():
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [], "passed_flows": [],
            "raw": "",
        }

    raw = report_path.read_text()
    total = _extract_int(raw, r"total_flows:\s*(\d+)")
    passed = _extract_int(raw, r"passed:\s*(\d+)")
    failed = _extract_int(raw, r"failed:\s*(\d+)")
    blocked = _extract_int(raw, r"blocked:\s*(\d+)")

    failed_flows = re.findall(r"###\s+FAIL:\s+([\w-]+)", raw)
    blocked_flows = re.findall(r"###\s+BLOCKED:\s+([\w-]+)", raw)
    passed_flows = re.findall(r"###\s+PASS:\s+([\w-]+)", raw)

    return {
        "total": total, "passed": passed, "failed": failed, "blocked": blocked,
        "failed_flows": failed_flows, "blocked_flows": blocked_flows,
        "passed_flows": passed_flows, "raw": raw,
    }


def _aggregate_reports(repo_path: str, batch_count: int) -> dict:
    """Merge all batch reports into a single TEST_REPORT.md and return summary."""
    total = passed = failed = blocked = 0
    all_failed: list[str] = []
    all_blocked: list[str] = []
    sections: list[str] = []

    for i in range(batch_count):
        report = _parse_batch_report(repo_path, i)
        total += report["total"]
        passed += report["passed"]
        failed += report["failed"]
        blocked += report["blocked"]
        all_failed.extend(report["failed_flows"])
        all_blocked.extend(report["blocked_flows"])
        if report["raw"]:
            sections.append(f"<!-- Batch {i} -->\n{report['raw']}")

    # Write aggregated report
    aggregated = f"""# E2E Test Report

## Summary
total_flows: {total}
passed: {passed}
failed: {failed}
blocked: {blocked}

## Batch Reports

{"---".join(sections)}

## Failed Flow Details (for fixer agent)
"""
    for fid in all_failed:
        aggregated += f"- {fid}: see batch report above\n"

    report_path = Path(repo_path) / "docs" / "TEST_REPORT.md"
    report_path.write_text(aggregated)

    return {
        "total": total, "passed": passed, "failed": failed, "blocked": blocked,
        "failed_flows": all_failed, "blocked_flows": all_blocked,
        "all_passed": failed == 0 and blocked == 0 and total > 0,
        "raw": aggregated,
    }


def parse_test_report(repo_path: str) -> dict:
    """Parse docs/TEST_REPORT.md and return structured results."""
    report_path = Path(repo_path) / "docs" / "TEST_REPORT.md"
    if not report_path.exists():
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [],
            "all_passed": False, "raw": "",
        }

    raw = report_path.read_text()
    total = _extract_int(raw, r"total_flows:\s*(\d+)")
    passed = _extract_int(raw, r"passed:\s*(\d+)")
    failed = _extract_int(raw, r"failed:\s*(\d+)")
    blocked = _extract_int(raw, r"blocked:\s*(\d+)")
    failed_flows = re.findall(r"###\s+FAIL:\s+([\w-]+)", raw)
    blocked_flows = re.findall(r"###\s+BLOCKED:\s+([\w-]+)", raw)

    return {
        "total": total, "passed": passed, "failed": failed, "blocked": blocked,
        "failed_flows": failed_flows, "blocked_flows": blocked_flows,
        "all_passed": failed == 0 and blocked == 0 and total > 0,
        "raw": raw,
    }


def _extract_int(text: str, pattern: str) -> int:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else 0


# ---------------------------------------------------------------------------
# Main runner (batched)
# ---------------------------------------------------------------------------

async def run_e2e_tests(
    repo_path: str,
    app_url: str,
    config: Config,
    reporter: StatusReporter,
    retest_only: list[str] | None = None,
) -> dict:
    """Run E2E tests against the live app in batches.

    Splits USER_FLOWS.md into batches of 6 flows. Each batch gets its own
    agent run with max_turns=25 to stay well under the 1MB context buffer.

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

    # Parse flows and create batches
    flows = parse_user_flows(user_flows_content)
    if not flows:
        print("[tester] No flows parsed from USER_FLOWS.md — skipping")
        await reporter.report("e2e_testing_skipped", {"reason": "no flows parsed"})
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [],
            "all_passed": True, "raw": "",
        }

    batches = batch_flows(flows, batch_size=6, retest_only=retest_only)
    total_batches = len(batches)
    print(f"[tester] Parsed {len(flows)} flows into {total_batches} batches")

    # Track results across batches for dependency resolution
    prior_results: dict[str, str] = {}
    total_cost = 0.0

    for batch_idx, batch in enumerate(batches):
        flow_ids = [f.flow_id for f in batch]
        batch_flows_content = "\n\n".join(f.raw_text for f in batch)

        print(f"[tester] Batch {batch_idx + 1}/{total_batches}: {', '.join(flow_ids)}")
        await reporter.report("e2e_batch_started", {
            "batch_idx": batch_idx,
            "total_batches": total_batches,
            "flow_ids": flow_ids,
            "flow_count": len(batch),
        })

        prompt = e2e_batch_tester_prompt(
            batch_flows_content=batch_flows_content,
            seed_data_content=seed_data_content,
            app_url=app_url,
            vp_script=config.vp_script_path,
            resend_api_key=config.resend_api_key,
            screenshots_dir=screenshots_dir,
            batch_idx=batch_idx,
            total_batches=total_batches,
            prior_results=prior_results if prior_results else None,
        )

        try:
            result = await run_agent(
                prompt=prompt,
                system_prompt=vp_system,
                allowed_tools=["Bash", "Read", "Write", "Grep", "Glob"],
                cwd=repo_path,
                model="claude-sonnet-4-6",
                max_turns=25,
                reporter=reporter,
                agent_label=f"e2e-batch-{batch_idx}",
            )
            total_cost += result.cost_usd

            # Parse batch report and update prior_results
            batch_report = _parse_batch_report(repo_path, batch_idx)

            for fid in batch_report.get("passed_flows", []):
                prior_results[fid] = "PASS"
                await reporter.report("e2e_flow_passed", {"flow_id": fid})
            for fid in batch_report.get("failed_flows", []):
                prior_results[fid] = "FAIL"
                await reporter.report("e2e_flow_failed", {"flow_id": fid})
            for fid in batch_report.get("blocked_flows", []):
                prior_results[fid] = "BLOCKED"
                await reporter.report("e2e_flow_blocked", {"flow_id": fid})

            # Mark any flows not reported (agent didn't get to them) as BLOCKED
            for f in batch:
                if f.flow_id not in prior_results:
                    prior_results[f.flow_id] = "BLOCKED"
                    await reporter.report("e2e_flow_blocked", {
                        "flow_id": f.flow_id,
                        "reason": "not reported by batch agent",
                    })

            await reporter.report("e2e_batch_completed", {
                "batch_idx": batch_idx,
                "total_batches": total_batches,
                "passed": batch_report.get("passed", 0),
                "failed": batch_report.get("failed", 0),
                "blocked": batch_report.get("blocked", 0),
                "cost_usd": result.cost_usd,
            })
            print(
                f"[tester] Batch {batch_idx + 1} done — "
                f"{batch_report.get('passed', 0)} pass, "
                f"{batch_report.get('failed', 0)} fail, "
                f"{batch_report.get('blocked', 0)} blocked"
            )

        except Exception as e:
            print(f"[tester] Batch {batch_idx + 1} error: {e}")
            await reporter.report("e2e_batch_failed", {
                "batch_idx": batch_idx,
                "total_batches": total_batches,
                "error": str(e)[:500],
            })
            # Mark all flows in this batch as blocked
            for f in batch:
                if f.flow_id not in prior_results:
                    prior_results[f.flow_id] = "BLOCKED"
                    await reporter.report("e2e_flow_blocked", {
                        "flow_id": f.flow_id,
                        "reason": f"batch {batch_idx} failed: {str(e)[:200]}",
                    })

    # Aggregate all batch reports into final TEST_REPORT.md
    report = _aggregate_reports(repo_path, total_batches)

    await reporter.report("e2e_testing_complete", {
        "total": report["total"],
        "passed": report["passed"],
        "failed": report["failed"],
        "blocked": report["blocked"],
        "all_passed": report["all_passed"],
        "cost_usd": total_cost,
        "batches": total_batches,
    })
    print(
        f"[tester] E2E complete — "
        f"{report['passed']}/{report['total']} passed, "
        f"{report['failed']} failed, {report['blocked']} blocked "
        f"(${total_cost:.2f} across {total_batches} batches)"
    )
    return report
