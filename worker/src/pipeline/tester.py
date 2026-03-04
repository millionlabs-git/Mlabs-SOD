"""Post-deploy E2E tester — runs USER_FLOWS.md against the live app with Visual Playwright.

Splits flows into batches of 6 to avoid context overflow. Each batch
gets its own agent run with max_turns=25 (instead of 50 for all flows).
"""
from __future__ import annotations

import json
import re
import subprocess
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
    """Parse USER_FLOWS.md into FlowSpec objects.

    Supports two formats:
      1. Structured: ``## Flow: <flow-id>`` with priority/user_type/depends_on fields
      2. Prose: ``### N.M <Flow Name>`` (numbered subsections under ``## N. Category``)

    The prose format is auto-detected when no ``## Flow:`` headers are found.
    """
    # Try structured format first
    flows = _parse_structured_flows(content)
    if flows:
        return flows

    # Fall back to prose/numbered format
    return _parse_prose_flows(content)


def _parse_structured_flows(content: str) -> list[FlowSpec]:
    """Parse ``## Flow: <id>`` format."""
    flows: list[FlowSpec] = []
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


def _parse_prose_flows(content: str) -> list[FlowSpec]:
    """Parse numbered prose format (``### N.M Name`` under ``## N. Category``).

    Derives flow_id from the name by slugifying it, and infers priority
    from the category (auth flows → critical, admin → medium, etc.).
    """
    flows: list[FlowSpec] = []

    # First pass: build a map of line_number → category from ## headers
    lines = content.split("\n")
    cat_at_line: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = re.match(r"^## \d+\.\s*(.+)", line)
        if m:
            cat_at_line.append((i, m.group(1).strip().lower()))

    def _category_for_line(line_no: int) -> str:
        cat = ""
        for cl, cn in cat_at_line:
            if cl <= line_no:
                cat = cn
        return cat

    # Second pass: split on ### headers
    chunks = re.split(r"(?=^### )", content, flags=re.MULTILINE)
    offset = 0

    for chunk in chunks:
        header = re.match(r"^###\s+(?:\d+\.\d+\s+)?(.+?)$", chunk, re.MULTILINE)
        if not header:
            offset += chunk.count("\n")
            continue

        name = header.group(1).strip()
        # Skip TOC entries (very short chunks)
        if len(chunk.strip().split("\n")) < 3:
            offset += chunk.count("\n")
            continue

        flow_id = _slugify(name)
        current_category = _category_for_line(offset)

        # Infer priority from category and name
        name_lower = name.lower()
        priority = "medium"
        if any(kw in current_category for kw in ["auth"]):
            priority = "critical"
        elif any(kw in name_lower for kw in ["login", "signup", "register", "logout"]):
            priority = "critical"
        elif any(kw in current_category for kw in ["admin", "moderat"]):
            priority = "medium"
        elif any(kw in current_category for kw in ["waitlist", "theme"]):
            priority = "low"

        # Infer user_type from category
        user_type = ""
        if "admin" in current_category:
            user_type = "admin"
        elif "account" in current_category:
            user_type = "user"
        elif "auth" in current_category:
            user_type = "user"

        flows.append(FlowSpec(
            flow_id=flow_id,
            priority=priority,
            user_type=user_type,
            depends_on=[],
            raw_text=chunk.strip(),
        ))

        offset += chunk.count("\n")

    return flows


def _slugify(name: str) -> str:
    """Convert a flow name to a slug ID: 'Add a Building (happy path)' → 'add-a-building-happy-path'."""
    slug = name.lower()
    slug = re.sub(r"[/:\\|]", " ", slug)  # common separators → space
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)  # remove remaining special chars
    slug = re.sub(r"[\s]+", "-", slug.strip())  # spaces to hyphens
    slug = re.sub(r"-+", "-", slug)  # collapse multiple hyphens
    return slug[:60]  # cap length


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

def _parse_batch_report(
    repo_path: str,
    batch_idx: int,
    known_flow_ids: list[str] | None = None,
) -> dict:
    """Parse a single batch report file.

    Uses header counting (``### PASS/FAIL/BLOCKED``) as the primary source
    of truth — agents rarely update the summary section reliably.
    When *known_flow_ids* is provided, fuzzy-matches the text after the
    status keyword back to the canonical slugified IDs.
    """
    report_path = Path(repo_path) / "docs" / f"TEST_REPORT_BATCH_{batch_idx}.md"
    if not report_path.exists():
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [], "passed_flows": [],
            "raw": "",
        }

    raw = report_path.read_text()

    # Primary: count by scanning ### headers (agents don't reliably update summary)
    passed_entries = re.findall(r"^###\s+PASS:\s*(.+)$", raw, re.MULTILINE)
    failed_entries = re.findall(r"^###\s+FAIL(?:ED)?:\s*(.+)$", raw, re.MULTILINE)
    blocked_entries = re.findall(r"^###\s+BLOCKED:\s*(.+)$", raw, re.MULTILINE)

    header_total = len(passed_entries) + len(failed_entries) + len(blocked_entries)

    if header_total > 0:
        passed = len(passed_entries)
        failed = len(failed_entries)
        blocked = len(blocked_entries)
        total = header_total
    else:
        # Fallback: trust summary counts only when no headers found
        total = _extract_int(raw, r"total_flows:\s*(\d+)")
        passed = _extract_int(raw, r"passed:\s*(\d+)")
        failed = _extract_int(raw, r"failed:\s*(\d+)")
        blocked = _extract_int(raw, r"blocked:\s*(\d+)")

    # Match report entries to known flow IDs
    passed_flows = _match_flow_ids(passed_entries, known_flow_ids)
    failed_flows = _match_flow_ids(failed_entries, known_flow_ids)
    blocked_flows = _match_flow_ids(blocked_entries, known_flow_ids)

    return {
        "total": total, "passed": passed, "failed": failed, "blocked": blocked,
        "failed_flows": failed_flows, "blocked_flows": blocked_flows,
        "passed_flows": passed_flows, "raw": raw,
    }


def _match_flow_ids(
    entries: list[str],
    known_ids: list[str] | None,
) -> list[str]:
    """Best-effort match of report header text to canonical flow IDs.

    Tries: exact match → slugified match → substring containment.
    Falls back to slugified entry if no known IDs are provided.
    """
    if not entries:
        return []

    def _clean(text: str) -> str:
        """Strip trailing timing info like (2.1s) and extra whitespace."""
        return re.sub(r"\s*\(\d+\.?\d*s?\)\s*$", "", text.strip())

    if not known_ids:
        return [_slugify(_clean(e)) for e in entries if e.strip()]

    known_set = set(known_ids)
    matched: list[str] = []
    for entry in entries:
        entry_clean = _clean(entry)
        if not entry_clean:
            continue

        # 1. Exact match (agent wrote the slug ID)
        if entry_clean in known_set:
            matched.append(entry_clean)
            continue

        # 2. Slugify the entry text and check
        slugified = _slugify(entry_clean)
        if slugified in known_set:
            matched.append(slugified)
            continue

        # 3. Substring containment (e.g. "signup-email" matches "signup-email-password")
        found = False
        for kid in known_ids:
            if kid in slugified or slugified in kid:
                matched.append(kid)
                found = True
                break

        # 4. Strip leading numbers/punctuation and re-slugify
        #    Handles "4.15 — Manage Duplicates: Merge" → "manage-duplicates-merge"
        if not found:
            stripped = re.sub(r"^[\d.]+\s*[-—–]?\s*", "", entry_clean)
            stripped_slug = _slugify(stripped)
            if stripped_slug in known_set:
                matched.append(stripped_slug)
                found = True
            else:
                for kid in known_ids:
                    if kid in stripped_slug or stripped_slug in kid:
                        matched.append(kid)
                        found = True
                        break

        if not found:
            matched.append(slugified or entry_clean)

    return matched


def _aggregate_reports(repo_path: str, batch_count: int) -> dict:
    """Merge all batch reports into a single TEST_REPORT.md and return summary."""
    total = passed = failed = blocked = 0
    all_failed: list[str] = []
    all_blocked: list[str] = []
    all_passed_flows: list[str] = []
    sections: list[str] = []

    for i in range(batch_count):
        report = _parse_batch_report(repo_path, i)
        total += report["total"]
        passed += report["passed"]
        failed += report["failed"]
        blocked += report["blocked"]
        all_failed.extend(report["failed_flows"])
        all_blocked.extend(report["blocked_flows"])
        all_passed_flows.extend(report.get("passed_flows", []))
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
    """Parse docs/TEST_REPORT.md and return structured results.

    Uses header counting as primary source of truth, with summary
    section as fallback.
    """
    report_path = Path(repo_path) / "docs" / "TEST_REPORT.md"
    if not report_path.exists():
        return {
            "total": 0, "passed": 0, "failed": 0, "blocked": 0,
            "failed_flows": [], "blocked_flows": [],
            "all_passed": False, "raw": "",
        }

    raw = report_path.read_text()

    # Primary: count headers
    passed_entries = re.findall(r"^###\s+PASS:\s*(.+)$", raw, re.MULTILINE)
    failed_entries = re.findall(r"^###\s+FAIL(?:ED)?:\s*(.+)$", raw, re.MULTILINE)
    blocked_entries = re.findall(r"^###\s+BLOCKED:\s*(.+)$", raw, re.MULTILINE)

    header_total = len(passed_entries) + len(failed_entries) + len(blocked_entries)

    if header_total > 0:
        passed = len(passed_entries)
        failed = len(failed_entries)
        blocked = len(blocked_entries)
        total = header_total
    else:
        total = _extract_int(raw, r"total_flows:\s*(\d+)")
        passed = _extract_int(raw, r"passed:\s*(\d+)")
        failed = _extract_int(raw, r"failed:\s*(\d+)")
        blocked = _extract_int(raw, r"blocked:\s*(\d+)")

    failed_flows = _match_flow_ids(failed_entries, None)
    blocked_flows = _match_flow_ids(blocked_entries, None)

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
# Pre-flight health check
# ---------------------------------------------------------------------------

def _preflight_check(app_url: str) -> tuple[bool, str]:
    """Quick health check before running E2E batches.

    Returns (ok, error_message). Checks:
    1. /health endpoint responds
    2. Auth endpoint responds (any non-timeout = healthy)
    """
    # Check /health (30s timeout to allow Fly.io cold starts)
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", "30",
                "-o", "/dev/null", "-w", "%{http_code}",
                f"{app_url}/health",
            ],
            capture_output=True, text=True, timeout=35,
        )
        status_code = result.stdout.strip()
        if not status_code or status_code == "000":
            return False, f"Health check failed: {app_url}/health — no response (app may be down)"
        # 200-499 means app is alive (even 404 means server responds)
        code = int(status_code)
        if code >= 500:
            return False, f"Health check failed: {app_url}/health returned {status_code}"
    except subprocess.TimeoutExpired:
        return False, f"Health check timed out: {app_url}/health did not respond within 30s"

    # Check auth endpoint (any response = healthy, only timeout = unhealthy)
    try:
        result = subprocess.run(
            [
                "curl", "-s", "--max-time", "30",
                "-X", "POST",
                "-H", "Content-Type: application/json",
                "-d", json.dumps({"email": "admin@test.mlabs.app", "password": "TestPass123!"}),
                "-o", "/dev/null", "-w", "%{http_code}",
                f"{app_url}/api/auth/login",
            ],
            capture_output=True, text=True, timeout=35,
        )
        # Any HTTP response (200, 401, 404) means the server is alive
        status_code = result.stdout.strip()
        if not status_code or status_code == "000":
            return False, f"Auth endpoint unreachable: {app_url}/api/auth/login returned no response"
    except subprocess.TimeoutExpired:
        return False, f"Auth endpoint timed out: {app_url}/api/auth/login did not respond within 30s"

    return True, ""


# ---------------------------------------------------------------------------
# Main runner (batched)
# ---------------------------------------------------------------------------

async def run_e2e_tests(
    repo_path: str,
    app_url: str,
    config: Config,
    reporter: StatusReporter,
    retest_only: list[str] | None = None,
    fly_app_name: str = "",
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

    # Pre-flight health check
    preflight_ok, preflight_error = _preflight_check(app_url)
    if not preflight_ok:
        print(f"[tester] Pre-flight check failed: {preflight_error}")
        await reporter.report("e2e_preflight_failed", {"error": preflight_error})
        # Write a diagnostic report so the fixer can act
        all_flow_ids = [f.flow_id for batch in batches for f in batch]
        total_flow_count = len(all_flow_ids)
        diagnostic = f"""# E2E Test Report

## Summary
total_flows: {total_flow_count}
passed: 0
failed: 0
blocked: {total_flow_count}

## Pre-flight Failure

The app failed health checks before any tests ran:
{preflight_error}

All {total_flow_count} flows marked BLOCKED.

## Failed Flow Details (for fixer agent)
- Pre-flight health check failed: {preflight_error}
"""
        report_path = Path(repo_path) / "docs" / "TEST_REPORT.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(diagnostic)

        return {
            "total": total_flow_count, "passed": 0, "failed": 0,
            "blocked": total_flow_count,
            "failed_flows": [], "blocked_flows": all_flow_ids,
            "all_passed": False, "raw": diagnostic,
        }

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
            fly_app_name=fly_app_name,
        )

        # Create skeleton report file BEFORE the agent runs — agents often
        # ignore the prompt instruction to create it and hit max_turns without
        # writing anything.  This ensures _parse_batch_report() always has a file.
        report_file = Path(repo_path) / "docs" / f"TEST_REPORT_BATCH_{batch_idx}.md"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(
            f"# E2E Test Report — Batch {batch_idx + 1} of {total_batches}\n\n"
            f"## Summary\n"
            f"batch: {batch_idx + 1}/{total_batches}\n"
            f"total_flows: 0\n"
            f"passed: 0\n"
            f"failed: 0\n"
            f"blocked: 0\n"
            f"app_url: {app_url}\n\n"
            f"## Results\n\n"
            f"## Failed Flow Details (for fixer agent)\n"
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
            batch_report = _parse_batch_report(
                repo_path, batch_idx,
                known_flow_ids=[f.flow_id for f in batch],
            )

            for fid in batch_report.get("passed_flows", []):
                prior_results[fid] = "PASS"
                await reporter.report("e2e_flow_passed", {"flow_id": fid})
            for fid in batch_report.get("failed_flows", []):
                prior_results[fid] = "FAIL"
                await reporter.report("e2e_flow_failed", {"flow_id": fid})
            for fid in batch_report.get("blocked_flows", []):
                prior_results[fid] = "BLOCKED"
                await reporter.report("e2e_flow_blocked", {"flow_id": fid})

            # Backfill: any flows not in the report get appended as BLOCKED
            # so _aggregate_reports can pick them up from the file.
            unreported = [f for f in batch if f.flow_id not in prior_results]
            if unreported:
                with open(report_file, "a") as rf:
                    for f in unreported:
                        rf.write(f"\n### BLOCKED: {f.flow_id}\n")
                        rf.write(f"  reason: not reported by batch agent (hit max_turns)\n")
                for f in unreported:
                    prior_results[f.flow_id] = "BLOCKED"
                    await reporter.report("e2e_flow_blocked", {
                        "flow_id": f.flow_id,
                        "reason": "not reported by batch agent",
                    })

            # Cascade abort: if batch 0 has 0 passes and >50% blocked/failed, abort
            if batch_idx == 0 and total_batches > 1:
                b0_passed = batch_report.get("passed", 0)
                b0_total = batch_report.get("total", 0) or len(batch)
                b0_bad = batch_report.get("failed", 0) + batch_report.get("blocked", 0)
                if b0_passed == 0 and b0_total > 0 and b0_bad > b0_total * 0.5:
                    abort_reason = (
                        f"Batch 0 critical auth flows: 0 passed, "
                        f"{batch_report.get('failed', 0)} failed, "
                        f"{batch_report.get('blocked', 0)} blocked"
                    )
                    print(f"[tester] ABORTING remaining batches — {abort_reason}")
                    await reporter.report("e2e_aborted", {"reason": abort_reason})

                    # Mark all remaining flows as BLOCKED
                    for remaining_batch in batches[1:]:
                        for f in remaining_batch:
                            if f.flow_id not in prior_results:
                                prior_results[f.flow_id] = "BLOCKED"
                                await reporter.report("e2e_flow_blocked", {
                                    "flow_id": f.flow_id,
                                    "reason": f"Aborted — critical auth flows failed in batch 0",
                                })

                    # Write partial report and return early
                    report = _aggregate_reports(repo_path, 1)  # Only batch 0 has a report file
                    # Adjust totals to include aborted flows
                    aborted_count = sum(len(b) for b in batches[1:])
                    report["total"] += aborted_count
                    report["blocked"] += aborted_count
                    report["blocked_flows"].extend(
                        f.flow_id for b in batches[1:] for f in b
                        if f.flow_id not in report.get("blocked_flows", [])
                    )
                    report["all_passed"] = False
                    # Re-write with abort info
                    report["raw"] += f"\n\n## Abort\n\n{abort_reason}\n"
                    (Path(repo_path) / "docs" / "TEST_REPORT.md").write_text(report["raw"])

                    await reporter.report("e2e_testing_complete", {
                        "total": report["total"],
                        "passed": report["passed"],
                        "failed": report["failed"],
                        "blocked": report["blocked"],
                        "all_passed": False,
                        "cost_usd": total_cost,
                        "batches": 1,
                        "aborted": True,
                        "abort_reason": abort_reason,
                    })
                    return report

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
