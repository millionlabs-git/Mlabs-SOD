# E2E Testing Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add post-deploy E2E testing with Visual Playwright interactive tests, test data seeding, Resend email verification, and an automated fix-retest loop.

**Architecture:** Three new pipeline steps (seed → test → fix loop) run after deploy in `runner.py`. The architect agent generates USER_FLOWS.md and SEED_DATA.md during planning. A new tester module executes flows via VP against the live URL. The fix-retest loop redeploys and retests up to 5 times.

**Tech Stack:** Python, Claude Agent SDK, Visual Playwright (VP), Resend API, existing pipeline infrastructure.

---

### Task 1: Add `resend_api_key` to Config

**Files:**
- Modify: `worker/src/config.py:30-31` (add field after `fly_api_token`)
- Modify: `worker/src/config.py:66-67` (add env var loading)

**Step 1: Add the config field**

In `worker/src/config.py`, add after line 31 (`fly_api_token: str = ""`):

```python
resend_api_key: str = ""
```

**Step 2: Add env var loading**

In `worker/src/config.py`, add after line 67 (`fly_api_token=...`):

```python
resend_api_key=os.environ.get("RESEND_API_KEY", ""),
```

**Step 3: Verify**

Run: `cd worker && python -c "from src.config import Config; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add worker/src/config.py
git commit -m "feat: add resend_api_key to worker config"
```

---

### Task 2: Update architect prompt to generate USER_FLOWS.md and SEED_DATA.md

**Files:**
- Modify: `worker/src/orchestrator/runner.py:42-83` (architect AgentDefinition)
- Create: `worker/src/prompts/testing.py` (new prompt templates)

**Step 1: Create testing prompts module**

Create `worker/src/prompts/testing.py`:

```python
from __future__ import annotations


def user_flows_instructions() -> str:
    """Instructions appended to the architect prompt for generating USER_FLOWS.md."""
    return """\

## Document 3: docs/USER_FLOWS.md

After writing ARCHITECTURE.md and BUILD_PLAN.md, generate a comprehensive E2E test manifest.
This file will be executed by an automated tester agent against the live deployed app using
Visual Playwright (browser automation).

### Format

```
# User Flows

## Meta
app_type: <type of application>
user_types:
  - role: <role_name>
    seed_account: <email> / <password>
    description: <what this user does in the app>

## Flow: <flow-id>
priority: critical | high | medium
user_type: <role>
depends_on: [<flow-ids that must pass first>]
preconditions:
  - <required state before this flow>
steps:
  - action: <goto|fill|click|wait|screenshot|select|check_email|assert>
    <action-specific params>
postconditions:
  - <expected state after flow completes>
```

### Action Types

- **goto**: Navigate to a URL. Params: `url`, `expect_visible` (text to verify page loaded)
- **fill**: Type into an input. Params: `selector` (comma-separated CSS selectors, tried left-to-right), `value`
- **click**: Click an element. Params: `selector` (comma-separated)
- **wait**: Wait for a condition. Params: `for` (visible|navigation|any), `timeout`, `conditions` (for `any`)
- **select**: Select from dropdown. Params: `selector`, `value`, optional `fallback: skip`
- **screenshot**: Capture state. Params: `name`
- **check_email**: Read sent email via Resend API. Params: `to`, `subject_contains`, `timeout`, `extract` (variable name for extracted URL)
- **assert**: Verify state. Params: `type` (visible|no_error|email_received), `selector`, `text_contains`

### Selector Strategy

Use comma-separated CSS selectors for resilience. Try data-testid first, then semantic HTML, then text:
`[data-testid="submit-btn"], button[type="submit"], button:has-text("Submit")`

### Requirements

- Cover EVERY user-facing feature for EVERY user type
- Include login/logout/register for each user type
- Include all CRUD operations visible to each role
- Include password reset (with check_email to follow reset link)
- Include email verification for registration (with check_email)
- Include cross-cutting: unauthenticated redirect, invalid credentials, session handling
- Use test email addresses on the pattern: <role>@test.mlabs.app
- Use `newuser@test.mlabs.app` for registration flow (NOT pre-seeded)
- All seeded accounts use password: TestPass123!
- Every flow that submits a form must verify the result (check it appears in a list, check success message, etc.)
- Flows that send transactional emails (password reset, verification, invites) must use check_email to read the email and follow the action link

Target: 30-40+ flows for a typical app.
"""


def seed_data_instructions() -> str:
    """Instructions appended to the architect prompt for generating SEED_DATA.md."""
    return """\

## Document 4: docs/SEED_DATA.md

Generate a test data seeding specification. This will be used by an automated seeder
to populate the database with test data before E2E tests run.

### Format

```
# Seed Data

## Email Config
provider: resend
api_key: ${RESEND_API_KEY}

## Accounts
All accounts use password: TestPass123!
All accounts are pre-verified (email_verified: true).

- <email>
  role: <role>
  display_name: <name>
  <role-specific fields>

## <Entity Type>
- <entity details with relationships to accounts>
```

### Requirements

- One verified account per user type defined in USER_FLOWS.md
- All accounts use password `TestPass123!` and are email-verified
- Use email pattern: <role>@test.mlabs.app
- Include `newuser@test.mlabs.app` as NOT pre-seeded (for registration testing)
- Include enough relational data for flows to execute:
  - If a tenant flow views a lease, seed a lease
  - If a landlord flow views requests, seed a request
  - If a user flow views a list, seed 2-3 items so the list isn't empty
- Reference the exact field names from ARCHITECTURE.md schemas
"""


def seed_prompt(seed_data_content: str, db_url: str) -> str:
    """Prompt for the seeder agent that populates test data."""
    return f"""\
You have a deployed app with database at the URL below. Seed the following test data.

Database URL: {db_url}

## Seed Data Spec

{seed_data_content}

## Instructions

1. Read the app's schema/models to understand exact table structure, column names, and types
2. Write a seed script (Node.js or Python) that:
   - Connects to the database directly using the DATABASE_URL
   - Hashes passwords using the same library the app uses (check package.json for bcrypt, argon2, etc.)
   - Sets email_verified=true (or equivalent) on all accounts
   - Creates all entities with correct foreign key relationships
   - Uses the app's actual field names and types (read the schema first)
3. Run the seed script
4. Verify by querying the database:
   - SELECT count(*) FROM users (or equivalent)
   - SELECT email, role FROM users
   - Confirm all accounts exist with correct roles
5. If the app uses an ORM (Prisma, Drizzle), prefer using the ORM's client for seeding
   to ensure proper type handling and relations

Print a summary of what was seeded when done.
"""


def e2e_tester_prompt(
    user_flows_content: str,
    seed_data_content: str,
    app_url: str,
    vp_script: str,
    resend_api_key: str,
    screenshots_dir: str,
    retest_only: list[str] | None = None,
) -> str:
    """Prompt for the E2E tester agent."""
    retest_section = ""
    if retest_only:
        flow_list = ", ".join(retest_only)
        retest_section = f"""
## Retest Mode

This is a retest iteration. Only run the following flows (previously failed/blocked):
{flow_list}

Also run 3 critical smoke flows (any login flow + one create + one view) to catch regressions.
"""

    return f"""\
You are an E2E tester. Execute the user flows below against the live app using Visual Playwright.

App URL: {app_url}
VP Script: {vp_script}
Screenshots Dir: {screenshots_dir}
Resend API Key: {resend_api_key}
{retest_section}
## User Flows

{user_flows_content}

## Test Credentials (from SEED_DATA.md)

{seed_data_content}

## Execution Rules

1. Run flows in dependency order (check `depends_on` for each flow)
2. For each flow, execute every step sequentially using Visual Playwright:
   - `goto`: `node {{vp_script}} goto "{{app_url}}{{url}}" --screenshot {{screenshots_dir}}/{{name}}.png`
   - `fill`: `node {{vp_script}} fill "{{selector}}" "{{value}}"`
   - `click`: `node {{vp_script}} click "{{selector}}"`
   - `wait`: Check for the expected condition by taking a screenshot and evaluating
   - `screenshot`: `node {{vp_script}} screenshot {{screenshots_dir}}/{{name}}.png`
   - `select`: `node {{vp_script}} select "{{selector}}" "{{value}}"`
   - `check_email`: Use curl to call Resend API:
     ```bash
     curl -s https://api.resend.com/emails \\
       -H "Authorization: Bearer {{resend_api_key}}" \\
       -H "Content-Type: application/json"
     ```
     Poll every 2 seconds up to timeout. Find email matching `to` and `subject_contains`.
     Extract the URL from the email HTML body for the `extract` variable.
   - `assert`: Verify by taking a screenshot and checking the page content

3. Selector resolution: each step has comma-separated selectors. Try each left-to-right.
   Use `node {{vp_script}} attrs "{{selector}}"` to check if an element exists.
   If none match, FAIL the step and list what IS visible on the page.

4. On step failure:
   - Take a screenshot: `node {{vp_script}} screenshot {{screenshots_dir}}/{{flow_id}}-fail.png`
   - Record the error details and page state
   - Read browser console: `node {{vp_script}} eval "JSON.stringify(window.__console_errors || [])"`
   - Skip remaining steps in this flow (mark as BLOCKED)
   - Continue to the next flow — NEVER stop on failure

5. If a flow's dependency failed, mark it BLOCKED without attempting it.
   Note if seeded data could make it independent.

6. After ALL flows complete, write docs/TEST_REPORT.md:

```markdown
# E2E Test Report

## Summary
total_flows: <N>
passed: <N>
failed: <N>
blocked: <N>
run_time: <duration>
app_url: {app_url}

## Results

### PASS: <flow-id> (<duration>)
All N steps passed.

### FAIL: <flow-id> (<duration>)
Failed at step N: <action> <selector>
  error: <what went wrong>
  selectors_tried: <list>
  page_state: <what's actually on the page>
  screenshot: <path>
  console_errors: <any JS errors>
  diagnosis: <your analysis of the likely root cause — which file/component is probably broken>

### BLOCKED: <flow-id>
  reason: <which dependency failed>
  recommendation: <could this be made independent?>

## Failed Flow Details (for fixer agent)
- <flow-id>: <one-line diagnosis with likely file/component>
```

7. Close VP sessions when done: `node {{vp_script}} close`
"""


def e2e_fix_prompt(test_report_content: str, iteration: int) -> str:
    """Prompt for the fixer agent to resolve E2E test failures."""
    return f"""\
The E2E tests found failures. Fix ALL of the following issues (iteration {iteration}/5).

## Test Report

{test_report_content}

## Instructions

1. Fix EVERY failed flow listed above, not just one
2. For each failure, read the diagnosis and fix the root cause:
   - "Element not found" → check if the component renders the expected element, add it
   - "API returns 500" → read the route handler, find the error, fix it
   - "Page blank / route not found" → check routing config, add the missing route
   - "Form submits but no persistence" → check the API handler writes to DB
   - "Email not sent" → check the email sending code uses Resend correctly
3. After fixing all issues, verify:
   - `npm run build` exits 0
   - `npm test` shows 0 failures
4. Commit: `git add -A && git commit -m "fix: resolve E2E test failures (iteration {iteration})"`

Fix the root cause, not the symptom. Do not add workarounds or skip tests.
"""
```

**Step 2: Update architect prompt in runner.py**

In `worker/src/orchestrator/runner.py`, modify the architect AgentDefinition (lines 42-83).
Add at the top of the file, after existing imports:

```python
from src.prompts.testing import user_flows_instructions, seed_data_instructions
```

Then append to the architect's prompt string (line 79, before the closing `"`):

```python
+ user_flows_instructions()
+ seed_data_instructions()
```

The architect prompt ending changes from:
```python
            "Write ARCHITECTURE.md first, then BUILD_PLAN.md."
```
to:
```python
            "Write ARCHITECTURE.md first, then BUILD_PLAN.md, then USER_FLOWS.md, then SEED_DATA.md."
            + user_flows_instructions()
            + seed_data_instructions()
```

**Step 3: Verify**

Run: `cd worker && python -c "from src.prompts.testing import user_flows_instructions, seed_data_instructions, seed_prompt, e2e_tester_prompt, e2e_fix_prompt; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add worker/src/prompts/testing.py worker/src/orchestrator/runner.py
git commit -m "feat: add testing prompts and update architect to generate USER_FLOWS.md + SEED_DATA.md"
```

---

### Task 3: Create the seeder module

**Files:**
- Create: `worker/src/pipeline/seeder.py`

**Step 1: Create the seeder module**

Create `worker/src/pipeline/seeder.py`:

```python
"""Post-deploy test data seeder — populates the DB with test accounts and data."""
from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.testing import seed_prompt
from src.prompts.system import load_skill
from src.pipeline.agent import run_agent


async def seed_test_data(
    repo_path: str,
    db_url: str,
    config: Config,
    reporter: StatusReporter,
) -> bool:
    """Seed test data into the deployed database.

    Reads docs/SEED_DATA.md from the repo, then runs an agent that
    writes and executes a seed script against the live DB.

    Returns True if seeding succeeded, False otherwise.
    """
    await reporter.report("seeding_started")
    print("[seeder] Starting test data seeding...")

    seed_data_path = Path(repo_path) / "docs" / "SEED_DATA.md"
    if not seed_data_path.exists():
        print("[seeder] No SEED_DATA.md found — skipping seeding")
        await reporter.report("seeding_skipped", {"reason": "no SEED_DATA.md"})
        return True  # Not a failure, just nothing to seed

    seed_data_content = seed_data_path.read_text()

    try:
        await run_agent(
            prompt=seed_prompt(seed_data_content, db_url),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            cwd=repo_path,
            model=config.model,
            max_turns=20,
            reporter=reporter,
        )
        await reporter.report("seeding_complete")
        print("[seeder] Test data seeded successfully")
        return True

    except Exception as e:
        print(f"[seeder] Seeding failed: {e}")
        await reporter.report("seeding_failed", {"error": str(e)[:500]})
        return False
```

**Step 2: Verify**

Run: `cd worker && python -c "from src.pipeline.seeder import seed_test_data; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add worker/src/pipeline/seeder.py
git commit -m "feat: add test data seeder module"
```

---

### Task 4: Create the E2E tester module

**Files:**
- Create: `worker/src/pipeline/tester.py`

**Step 1: Create the tester module**

Create `worker/src/pipeline/tester.py`:

```python
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
```

**Step 2: Verify**

Run: `cd worker && python -c "from src.pipeline.tester import run_e2e_tests, parse_test_report; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add worker/src/pipeline/tester.py
git commit -m "feat: add E2E tester module with VP integration"
```

---

### Task 5: Create the fix-retest loop module

**Files:**
- Create: `worker/src/pipeline/e2e_loop.py`

**Step 1: Create the loop module**

Create `worker/src/pipeline/e2e_loop.py`:

```python
"""Fix-retest loop — runs E2E tests, fixes failures, redeploys, retests."""
from __future__ import annotations

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
    max_iterations: int = 5,
) -> dict:
    """Run the fix-retest loop until all E2E tests pass or max iterations hit.

    Flow:
    1. Run E2E tests
    2. If all pass → done
    3. If failures → fixer fixes all → redeploy → retest (only failed + smoke)
    4. Repeat up to max_iterations

    Returns the final test report dict.
    """
    await reporter.report("e2e_loop_started", {"max_iterations": max_iterations})
    print(f"[e2e-loop] Starting fix-retest loop (max {max_iterations} iterations)")

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
            **_report_summary(report),
        })
        return report

    # Fix-retest loop
    for iteration in range(2, max_iterations + 1):
        print(f"[e2e-loop] Iteration {iteration}/{max_iterations} — fixing {report['failed']} failures...")
        await reporter.report("e2e_fix_started", {
            "iteration": iteration,
            "failed_flows": report["failed_flows"],
        })

        # Run fixer agent on all failures
        try:
            await run_agent(
                prompt=e2e_fix_prompt(report["raw"], iteration),
                allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
                cwd=repo_path,
                model=config.model,
                max_turns=30,
                reporter=reporter,
            )
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
            print(f"[e2e-loop] All tests passed after {iteration} iterations!")
            await reporter.report("e2e_loop_complete", {
                "iterations": iteration,
                "result": "all_passed",
                **_report_summary(report),
            })
            return report

    # Max iterations exhausted
    print(f"[e2e-loop] Max iterations reached. {report['failed']} flows still failing.")
    await reporter.report("e2e_loop_complete", {
        "iterations": max_iterations,
        "result": "max_iterations_reached",
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
```

**Step 2: Verify**

Run: `cd worker && python -c "from src.pipeline.e2e_loop import run_e2e_loop; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add worker/src/pipeline/e2e_loop.py
git commit -m "feat: add fix-retest loop for E2E testing"
```

---

### Task 6: Integrate seed → test → loop into runner.py

**Files:**
- Modify: `worker/src/orchestrator/runner.py:580-604` (post-orchestrator section)

**Step 1: Add imports**

Add after the existing imports at the top of `runner.py` (around line 24):

```python
from src.pipeline.seeder import seed_test_data
from src.pipeline.tester import run_e2e_tests
from src.pipeline.e2e_loop import run_e2e_loop
```

**Step 2: Add seed + E2E loop after deploy**

Replace the section at lines 580-604 (the post-orchestrator deployment block) with:

```python
    # ── Post-orchestrator: handle deployment ──────────────────────────
    # Deployment is still handled by the existing deployer module because
    # it needs MCP servers (Neon) and special env var handling that are
    # easier to manage from Python.
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
        except Exception as exc:
            progress.fail_phase("deployment", str(exc))
            progress.save()
            raise

        # ── Post-deploy: Seed test data ──────────────────────────────
        if deploy_result and deploy_result.get("neon_project_id"):
            progress.start_phase("seeding")
            try:
                # Read DB URL from the deploy artifacts
                import json as _json
                creds_file = Path("/tmp/neon-credentials.json")
                db_url = None
                if creds_file.exists():
                    db_url = _json.loads(creds_file.read_text()).get("database_url")

                if db_url:
                    seed_ok = await seed_test_data(
                        repo_path, db_url, config, reporter
                    )
                    if seed_ok:
                        progress.complete_phase("seeding")
                    else:
                        progress.fail_phase("seeding", "Seeding returned False")
                else:
                    print("[runner] No DB URL available — skipping seeding")
                    progress.skip_phase("seeding")

                progress.save()
            except Exception as exc:
                progress.fail_phase("seeding", str(exc))
                progress.save()
                # Seeding failure is non-fatal — continue to E2E tests
                print(f"[runner] Seeding failed (non-fatal): {exc}")

        # ── Post-deploy: E2E testing loop ────────────────────────────
        live_url = deploy_result.get("live_url") if deploy_result else None
        fly_app_name = deploy_result.get("fly_app_name") if deploy_result else None

        if live_url and fly_app_name:
            progress.start_phase("e2e_testing")
            try:
                # Set RESEND_API_KEY as fly secret for the app
                if config.resend_api_key:
                    import os
                    os.environ.setdefault("FLY_API_TOKEN", config.fly_api_token)
                    import subprocess
                    subprocess.run(
                        ["flyctl", "secrets", "set",
                         f"RESEND_API_KEY={config.resend_api_key}",
                         "-a", fly_app_name],
                        capture_output=True, text=True, timeout=30,
                    )

                test_report = await run_e2e_loop(
                    repo_path=repo_path,
                    app_url=live_url,
                    fly_app_name=fly_app_name,
                    config=config,
                    reporter=reporter,
                    branch_name=branch_name,
                    max_iterations=5,
                )

                # Commit test artifacts
                git_commit(repo_path, "docs: add E2E test report and screenshots")
                if branch_name:
                    git_push(repo_path, branch_name)

                if test_report["all_passed"]:
                    progress.complete_phase("e2e_testing")
                else:
                    progress.fail_phase("e2e_testing",
                        f"{test_report['failed']} flows still failing")

                progress.save()
            except Exception as exc:
                progress.fail_phase("e2e_testing", str(exc))
                progress.save()
                print(f"[runner] E2E testing failed: {exc}")

    elif skip.get("deployment"):
        progress.skip_phase("deployment")

    return deploy_result
```

**Step 3: Verify**

Run: `cd worker && python -c "from src.orchestrator.runner import run_pipeline; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add worker/src/orchestrator/runner.py
git commit -m "feat: integrate seed + E2E test loop into pipeline runner"
```

---

### Task 7: Update orchestrator prompt to reference new phases

**Files:**
- Modify: `worker/src/orchestrator/runner.py:459-463` (Phase 5: Finalize section in prompt)

**Step 1: Update the orchestrator prompt**

In `_build_orchestrator_prompt()`, the Phase 5 section (lines 459-463) currently says:

```python
### Phase 5: Finalize
1. Final deploy: `flyctl deploy --app <app-name>` (if deploy enabled)
2. Commit: `git add -A && git commit -m "docs: finalize"`
3. Push: `git push origin {branch_name}`
4. Create PR: `gh pr create --title "<descriptive title>" --body-file docs/PR_DESCRIPTION.md --base main --head {branch_name}`
```

Replace with:

```python
### Phase 5: Finalize
1. Commit: `git add -A && git commit -m "docs: finalize"`
2. Push: `git push origin {branch_name}`
3. Create PR: `gh pr create --title "<descriptive title>" --body-file docs/PR_DESCRIPTION.md --base main --head {branch_name}`

**NOTE:** Deployment, test data seeding, and E2E testing are handled automatically
by the pipeline after the orchestrator completes. Do NOT deploy yourself in Phase 5.
The PR description should mention that E2E tests will be run post-deploy.
```

Also update the architect instructions to mention the new documents. In the architect's description (line 46-49), update to:

```python
        description=(
            "System architect and planner. Use this agent to design the "
            "technical architecture AND decompose it into ordered build tasks. "
            "It writes docs/ARCHITECTURE.md, docs/BUILD_PLAN.md, "
            "docs/USER_FLOWS.md, and docs/SEED_DATA.md."
        ),
```

**Step 2: Verify**

Run: `cd worker && python -c "from src.orchestrator.runner import _build_orchestrator_prompt; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add worker/src/orchestrator/runner.py
git commit -m "feat: update orchestrator prompt for new pipeline phases"
```

---

### Task 8: Pass RESEND_API_KEY to deployed apps via Fly secrets

**Files:**
- Modify: `worker/src/prompts/deploy.py:91-229` (flyio_deploy_prompt)

**Step 1: Update flyio_deploy_prompt**

In `worker/src/prompts/deploy.py`, modify `flyio_deploy_prompt()` to accept and set the Resend key.

Change the function signature (line 91) to:

```python
def flyio_deploy_prompt(job_id: str, db_url: str | None, resend_api_key: str = "") -> str:
```

Add after `db_secret_hint` (line 96):

```python
    resend_hint = ""
    if resend_api_key:
        resend_hint = f'\nflyctl secrets set RESEND_API_KEY="{resend_api_key}" -a {app_name}'
```

And in the secrets section of the prompt (around line 176), change:

```python
{db_secret_hint}
```

to:

```python
{db_secret_hint}{resend_hint}
```

**Step 2: Update the deployer to pass resend_api_key**

In `worker/src/pipeline/deployer.py`, line 281, update the `flyio_deploy_prompt` call:

```python
        prompt=flyio_deploy_prompt(config.job_id, db_url, config.resend_api_key),
```

**Step 3: Verify**

Run: `cd worker && python -c "from src.prompts.deploy import flyio_deploy_prompt; print(flyio_deploy_prompt('test123', None, 're_test')[:100])"`
Expected: First 100 chars of the prompt (no errors)

**Step 4: Commit**

```bash
git add worker/src/prompts/deploy.py worker/src/pipeline/deployer.py
git commit -m "feat: pass RESEND_API_KEY to deployed apps via Fly secrets"
```

---

### Task 9: Update dashboard to show new pipeline phases

**Files:**
- Modify: `src/routes/dashboard.ts` (PHASE_MAP and event styling in the HTML)

**Step 1: Add new events to PHASE_MAP**

In the dashboard HTML JavaScript, find the `PHASE_MAP` object and add:

```javascript
'seeding_started': 'seed',
'seeding_complete': 'seed',
'seeding_skipped': 'seed',
'seeding_failed': 'seed',
'e2e_testing_started': 'test',
'e2e_testing_complete': 'test',
'e2e_testing_skipped': 'test',
'e2e_testing_failed': 'test',
'e2e_loop_started': 'test',
'e2e_loop_complete': 'test',
'e2e_fix_started': 'fix',
'e2e_fix_failed': 'fix',
'e2e_redeploy_started': 'fix',
'e2e_redeploy_complete': 'fix',
'e2e_redeploy_failed': 'fix',
```

**Step 2: Add phases to the sidebar**

Add `seed` and `test` phases to the phase timeline in the sidebar HTML, between `deploy` and `done`:

```html
<div class="phase" data-phase="seed">Seed Data</div>
<div class="phase" data-phase="test">E2E Tests</div>
<div class="phase" data-phase="fix">Fix Loop</div>
```

**Step 3: Add event summarization for new events**

In the `summarize()` function, add cases:

```javascript
case 'seeding_started': return 'Seeding test data...';
case 'seeding_complete': return 'Test data seeded successfully';
case 'seeding_failed': return `Seeding failed: ${d.error || 'unknown'}`;
case 'e2e_testing_started': return `Starting E2E tests (${d.mode || 'full'})`;
case 'e2e_testing_complete':
  return `E2E: ${d.passed}/${d.total} passed, ${d.failed} failed` +
    (d.all_passed ? ' ✓' : '');
case 'e2e_loop_started': return `Fix-retest loop (max ${d.max_iterations} iterations)`;
case 'e2e_loop_complete':
  return `E2E loop done after ${d.iterations} iterations: ${d.result}`;
case 'e2e_fix_started':
  return `Fixing ${d.failed_flows?.length || 0} failed flows (iteration ${d.iteration})`;
case 'e2e_redeploy_started': return `Redeploying (iteration ${d.iteration})`;
case 'e2e_redeploy_complete': return `Redeploy complete (iteration ${d.iteration})`;
```

**Step 4: Verify**

Run: `npm run build`
Expected: exit 0

**Step 5: Commit**

```bash
git add src/routes/dashboard.ts
git commit -m "feat: add seed/test/fix phases to pipeline dashboard"
```

---

### Task 10: Add tests for tester report parser

**Files:**
- Create: `worker/tests/test_tester.py`

**Step 1: Write tests**

Create `worker/tests/test_tester.py`:

```python
"""Tests for the E2E tester report parser."""
import tempfile
from pathlib import Path

import pytest

from src.pipeline.tester import parse_test_report


class TestParseTestReport:
    """Tests for parse_test_report()."""

    def test_no_report_file(self, tmp_path: Path):
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 0
        assert result["all_passed"] is False
        assert result["failed_flows"] == []

    def test_all_passed(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 10\n"
            "passed: 10\n"
            "failed: 0\n"
            "blocked: 0\n\n"
            "## Results\n\n"
            "### PASS: tenant-login (2.1s)\nAll steps passed.\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 10
        assert result["passed"] == 10
        assert result["failed"] == 0
        assert result["blocked"] == 0
        assert result["all_passed"] is True

    def test_with_failures(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 10\n"
            "passed: 7\n"
            "failed: 2\n"
            "blocked: 1\n\n"
            "## Results\n\n"
            "### PASS: tenant-login (2.1s)\n\n"
            "### FAIL: create-building (5.2s)\n"
            "Failed at step 4\n\n"
            "### FAIL: password-reset (3.1s)\n"
            "Failed at step 6\n\n"
            "### BLOCKED: view-buildings\n"
            "reason: depends on create-building\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 10
        assert result["passed"] == 7
        assert result["failed"] == 2
        assert result["blocked"] == 1
        assert result["all_passed"] is False
        assert result["failed_flows"] == ["create-building", "password-reset"]
        assert result["blocked_flows"] == ["view-buildings"]

    def test_zero_total_not_passed(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 0\n"
            "passed: 0\n"
            "failed: 0\n"
            "blocked: 0\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["all_passed"] is False  # 0 total means nothing ran
```

**Step 2: Run tests**

Run: `cd worker && python -m pytest tests/test_tester.py -v`
Expected: 4 tests pass

**Step 3: Commit**

```bash
git add worker/tests/test_tester.py
git commit -m "test: add tests for E2E test report parser"
```

---

### Task 11: Build, deploy orchestrator, and rebuild worker image

**Files:** No file changes — deployment only.

**Step 1: Build orchestrator**

Run: `npm run build`
Expected: exit 0

**Step 2: Run orchestrator tests**

Run: `npm test` (if tests exist)

**Step 3: Run worker tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All tests pass

**Step 4: Deploy orchestrator to Fly**

Run: `flyctl deploy`

**Step 5: Rebuild worker image**

Run: `cd worker && gcloud builds submit --tag gcr.io/mlabs-sod/prd-worker:latest`

**Step 6: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: complete E2E testing loop pipeline"
```
