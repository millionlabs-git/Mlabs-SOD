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


def e2e_batch_tester_prompt(
    batch_flows_content: str,
    seed_data_content: str,
    app_url: str,
    vp_script: str,
    resend_api_key: str,
    screenshots_dir: str,
    batch_idx: int,
    total_batches: int,
    prior_results: dict[str, str] | None = None,
) -> str:
    """Prompt for the E2E tester agent scoped to a single batch of flows."""
    prior_results_section = ""
    if prior_results:
        lines = [
            f"  - {flow_id}: {status}"
            for flow_id, status in prior_results.items()
        ]
        prior_results_section = f"""
## Prior Batch Results

The following flows were executed in earlier batches. Use this to resolve dependencies:

{chr(10).join(lines)}

If a flow in this batch depends on a flow that FAILED or was BLOCKED in a prior batch,
mark it BLOCKED without attempting it.
"""

    return f"""\
You are an E2E tester. You are running batch {batch_idx + 1} of {total_batches}.
Execute ONLY the user flows listed below against the live app using Visual Playwright.

App URL: {app_url}
VP Script: {vp_script}
Screenshots Dir: {screenshots_dir}
Resend API Key: {resend_api_key}
{prior_results_section}
## Flows in This Batch

{batch_flows_content}

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

5. If a flow's dependency failed (in this batch or a prior batch), mark it BLOCKED without attempting it.
   Note if seeded data could make it independent.

6. After ALL flows in this batch complete, write docs/TEST_REPORT_BATCH_{batch_idx}.md:

```markdown
# E2E Test Report — Batch {batch_idx + 1} of {total_batches}

## Summary
batch: {batch_idx + 1}/{total_batches}
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
  reason: <which dependency failed, and whether it was in this batch or a prior batch>
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
