"""Orchestrator pipeline runner — a Claude agent with specialized subagents.

Instead of Python manually calling each phase, this launches a single
orchestrator agent that has access to specialized subagents via the
Claude Agent SDK's native AgentDefinition/Task tool pattern.  The
orchestrator reads the PRD, understands the project, and delegates to
subagents — deciding when and how to invoke each one.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from claude_agent_sdk import AgentDefinition

from src.config import Config
from src.status import StatusReporter
from src.orchestrator.progress import ProgressTracker
from src.orchestrator.tech_detector import detect_tech_stack, TechProfile
from src.orchestrator.component_loader import ComponentLoader
from src.orchestrator.context import ContextBuilder
from src.pipeline.agent import run_agent
from src.repo import git_commit, git_push
from src.prompts.testing import user_flows_instructions, seed_data_instructions


def _build_subagents(
    loader: ComponentLoader,
    context_builder: ContextBuilder,
    config: Config,
    tech_profile: TechProfile,
    repo_path: str,
    has_db: bool,
) -> dict[str, AgentDefinition]:
    """Build the AgentDefinition map — 5 consolidated subagents."""

    vp_script = config.vp_script_path
    screenshots_base = f"{repo_path}/docs/screenshots"

    agents: dict[str, AgentDefinition] = {}

    # ── Architect (absorbs planner) ───────────────────────────────────
    architect_system = loader.for_architect()
    agents["architect"] = AgentDefinition(
        description=(
            "System architect and planner. Use this agent to design the "
            "technical architecture AND decompose it into ordered build tasks. "
            "It writes docs/ARCHITECTURE.md, docs/BUILD_PLAN.md, "
            "docs/USER_FLOWS.md, and docs/SEED_DATA.md."
        ),
        prompt=(
            f"{architect_system}\n\n" if architect_system else ""
        ) + (
            "You are an expert software architect. Read the PRD provided "
            "and produce FOUR documents (all four are MANDATORY):\n\n"
            "## Document 1: docs/ARCHITECTURE.md\n"
            "Decide on:\n"
            "- Tech stack (languages, frameworks, databases)\n"
            "- Directory structure\n"
            "- Major components and responsibilities\n"
            "- Data models and schemas (ALL tables, ALL columns, ALL relations)\n"
            "- API contracts (every endpoint, method, request/response shapes)\n"
            "- Frontend routes and pages (every page, its purpose, key components)\n"
            "- Authentication and authorization approach\n"
            "- Error handling strategy\n\n"
            "## Document 2: docs/BUILD_PLAN.md\n"
            "Break the implementation into 8-15 ordered tasks.\n\n"
            "Each task should be a complete vertical slice — not just "
            "'create file X'. It should produce working, connected code.\n\n"
            "Order: database layer → API routes → frontend pages → integration.\n\n"
            "Use this exact format for each task:\n\n"
            "## Task N: <name>\n"
            "- **Description:** 2-4 sentences with specifics\n"
            "- **Files:** files to create/modify\n"
            "- **Dependencies:** task numbers or None\n"
            "- **Has UI:** true/false\n"
            "- **Route:** /path (if UI)\n"
            "- **Acceptance Criteria:**\n"
            "  - Specific, testable criterion\n\n"
            "## Document 3: docs/USER_FLOWS.md\n"
            "E2E test flows for every user-facing feature. This file is used by "
            "an automated tester agent to verify the deployed app works. "
            "YOU MUST WRITE THIS FILE — without it, E2E testing is skipped "
            "and the build is considered incomplete.\n\n"
            "## Document 4: docs/SEED_DATA.md\n"
            "Test data seeding manifest with accounts for every user type. "
            "This file is used to seed the production database before E2E testing. "
            "YOU MUST WRITE THIS FILE — without it, test data seeding is skipped.\n\n"
            "Write them in order: ARCHITECTURE.md → BUILD_PLAN.md → USER_FLOWS.md → SEED_DATA.md.\n"
            "Do NOT stop after BUILD_PLAN.md. All four documents are required.\n\n"
            "Detailed format instructions for USER_FLOWS.md and SEED_DATA.md follow:\n"
            + user_flows_instructions() + seed_data_instructions()
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="opus",
    )

    # ── Scaffolder ────────────────────────────────────────────────────
    scaffolder_system = loader.for_scaffolder()
    scaffolder_context = context_builder.for_scaffolder()
    agents["scaffolder"] = AgentDefinition(
        description=(
            "Project scaffolder. Use this agent to create the full project "
            "skeleton — directory structure, package.json with ALL deps, "
            "configs, database schema, route stubs, shared types, test infra. "
            "It must build clean with zero errors and npm test must pass."
        ),
        prompt=(
            f"{scaffolder_system}\n\n" if scaffolder_system else ""
        ) + (
            f"## Project Context\n\n{scaffolder_context}\n\n---\n\n"
            if scaffolder_context else ""
        ) + (
            "Create the full project scaffold based on the architecture and "
            "build plan above (also available in docs/ARCHITECTURE.md and "
            "docs/BUILD_PLAN.md).\n\n"
            "1. Directory structure matching the architecture exactly\n"
            "2. Package manager config with ALL dependencies for the full build\n"
            "3. Language configs (tsconfig.json, .eslintrc, etc.)\n"
            "4. Database schema/ORM models — all tables, all columns, all relations\n"
            "5. API route stubs with correct paths, methods, parameter types\n"
            "6. Shared types/interfaces\n"
            "7. Test infrastructure\n"
            "8. E2E test infrastructure:\n"
            "   - Install vitest (or jest) + supertest for API integration testing\n"
            "   - Create a test helper that starts the app server and provides a supertest instance\n"
            "   - Create a test DB setup/teardown helper (or in-memory SQLite fallback)\n"
            "   - Add a working example integration test that hits a health/root endpoint\n"
            "   - Ensure `npm test` works and passes from the start\n"
            "   - Install @playwright/test for browser e2e (configured but no tests yet)\n"
            "9. .env.example with all required vars\n\n"
            "Rules:\n"
            "- Install ALL deps and verify `npm run build` (or equivalent) succeeds\n"
            "- `npm test` MUST pass before scaffold is complete — run it and paste the output\n"
            "- No TODO comments — use minimal valid implementations instead\n"
            "- Define real data models with all fields, not just id and name"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Builder (feature implementer) ─────────────────────────────────
    builder_system = loader.for_builder()
    builder_context = context_builder.for_builder(
        task_name="", task_files=[], completed_tasks=[]
    )
    agents["builder"] = AgentDefinition(
        description=(
            "Feature builder. Use this agent to implement a specific task "
            "from the build plan. Give it the task number, name, description, "
            "and acceptance criteria. It writes complete, working code — no "
            "TODOs, no stubs, fully wired up."
        ),
        prompt=(
            f"{builder_system}\n\n" if builder_system else ""
        ) + (
            f"## Project Context\n\n{builder_context}\n\n---\n\n"
            if builder_context else ""
        ) + (
            "You are a feature implementer who works with TEST-DRIVEN DEVELOPMENT.\n\n"
            "## TDD Cycle (mandatory for every task)\n\n"
            "For each feature in the task:\n"
            "1. **RED** — Write a failing API integration test FIRST:\n"
            "   - Use supertest to hit the real endpoint with real DB\n"
            "   - Test the actual behavior: POST creates, GET retrieves, etc.\n"
            "   - Run the test. Watch it FAIL. Confirm it fails for the RIGHT reason.\n"
            "   - If the test passes immediately, it proves nothing — delete it and write a real one.\n\n"
            "2. **GREEN** — Write the MINIMUM code to make the test pass:\n"
            "   - Implement the route, controller, DB query — whatever the test needs\n"
            "   - Wire everything together: route → handler → DB → response\n"
            "   - Run the test again. It MUST pass now.\n\n"
            "3. **REFACTOR** — Clean up while tests stay green:\n"
            "   - Remove duplication, improve naming\n"
            "   - Run tests after each change to confirm nothing broke\n\n"
            "## Verification Gate (before claiming done)\n\n"
            "You MUST run these commands and paste the ACTUAL output:\n"
            "```\n"
            "npm run build    # Must exit 0\n"
            "npm test         # Must show 0 failures\n"
            "```\n"
            "If you haven't pasted real output from these commands, you are NOT done.\n"
            "NO 'should work', 'looks correct', 'probably passes' — EVIDENCE ONLY.\n\n"
            "## Testing Rules\n\n"
            "- Test REAL behavior, not mocks. Hit real endpoints, query real DB.\n"
            "- Every API route must have an integration test.\n"
            "- Every DB write must be verified with a read-back in the test.\n"
            "- Do NOT mock the database or HTTP layer in integration tests.\n"
            "- Tests written AFTER code pass immediately — that proves nothing.\n\n"
            "## Implementation Rules\n\n"
            "- No placeholder returns, no // TODO, no pass stubs\n"
            "- Wire everything: API → DB, UI → API, imports → usage\n"
            "- Use dependencies already in package.json\n"
            "- Search for TODO/FIXME/placeholder and replace with real code"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Fixer (absorbs build-error-resolver) ──────────────────────────
    fixer_system = loader.for_build_error_resolver()
    agents["fixer"] = AgentDefinition(
        description=(
            "Fixer. Use this agent when builds fail, tests fail, deploys fail, "
            "or review finds critical issues. Give it the error output or issue "
            "description and it will diagnose and fix systematically."
        ),
        prompt=(
            f"{fixer_system}\n\n" if fixer_system else ""
        ) + (
            "You fix build, test, deploy, and review failures using SYSTEMATIC DEBUGGING.\n\n"
            "## Phase 1: Investigate (before ANY fix)\n"
            "- Read the FULL error message carefully\n"
            "- Reproduce: run the failing command yourself\n"
            "- Gather evidence: which file, which line, what was expected vs actual\n"
            "- Check recent changes: what was the last thing modified?\n\n"
            "## Phase 2: Analyze\n"
            "- Find a WORKING example of similar code in the project\n"
            "- Compare the working code with the broken code line by line\n"
            "- Identify the specific difference causing the failure\n\n"
            "## Phase 3: Fix (one change at a time)\n"
            "- Form ONE hypothesis about the root cause\n"
            "- Make ONE targeted change\n"
            "- Run the failing command again to verify\n"
            "- If it still fails, REVERT and try a different hypothesis\n\n"
            "## Phase 4: Escalate\n"
            "- After 3 failed fix attempts, STOP patching\n"
            "- The problem is likely architectural, not a typo\n"
            "- Read the architecture doc and reconsider the approach\n"
            "- Report what you've tried and what you think the real issue is\n\n"
            "## Verification\n"
            "After fixing, run BOTH:\n"
            "```\n"
            "npm run build    # Must exit 0\n"
            "npm test         # Must show 0 failures\n"
            "```\n"
            "Paste the actual output. No assumptions."
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Reviewer (absorbs code/security/db reviewers + e2e + pr-writer) ─
    reviewer_system = loader.for_reviewer()
    reviewer_context = context_builder.for_reviewer()
    db_instructions = ""
    if has_db:
        db_instructions = (
            "\n## Database Review\n"
            "- Schema design (normalization, indexes, constraints)\n"
            "- Query patterns (N+1 problems, missing indexes)\n"
            "- Migration safety\n"
            "- Connection pooling and error handling\n"
        )
    agents["reviewer"] = AgentDefinition(
        description=(
            "Reviewer. Use this agent for the full review phase: code quality, "
            "security audit, database review, Playwright e2e browser tests, and "
            "PR description generation. One agent, one pass over the codebase."
        ),
        prompt=(
            f"{reviewer_system}\n\n" if reviewer_system else ""
        ) + (
            f"## Project Context\n\n{reviewer_context}\n\n---\n\n"
            if reviewer_context else ""
        ) + (
            "You are a comprehensive reviewer. Perform ALL of the following "
            "in a single pass:\n\n"
            "## 1. Code Quality Review\n"
            "- Code quality and maintainability\n"
            "- Error handling completeness\n"
            "- Test coverage gaps\n"
            "- Performance concerns\n\n"
            "## 2. Security Audit\n"
            "- Hardcoded secrets or credentials\n"
            "- Injection vulnerabilities (SQL, command, XSS)\n"
            "- Authentication and authorization issues\n"
            "- Dependency vulnerabilities (run npm audit if applicable)\n"
            "- Insecure configurations\n"
            "- Missing input validation\n"
            f"{db_instructions}\n"
            "## 3. Playwright E2E Browser Tests\n"
            "- Start the dev server (or use deployed URL if provided)\n"
            f"- Use Visual Playwright: node {vp_script} goto \"<url>\" "
            f"--screenshot {screenshots_base}/e2e/<page>.png\n"
            "- Navigate every route listed in ARCHITECTURE.md\n"
            "- Test key user flows (signup, login, CRUD operations)\n"
            "- Take screenshots of each page\n"
            f"- Close sessions: node {vp_script} close\n\n"
            "## 4. Write Review Documents\n"
            "- Write CODE_REVIEW.md with file:line references for all findings\n"
            "- Write PR_DESCRIPTION.md summarizing what was built\n"
            "- Fix critical issues directly. Document minor issues in the review.\n"
            f"Keep screenshots in {screenshots_base}/e2e/"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="opus",
    )

    return agents


def _build_orchestrator_prompt(
    prd_content: str,
    repo_path: str,
    config: Config,
    branch_name: str,
    skip: dict[str, bool],
    has_db: bool,
) -> str:
    """Build the main prompt for the orchestrator agent."""

    skip_instructions = ""
    if skip:
        skipped = [phase for phase, done in skip.items() if done]
        if skipped:
            skip_instructions = (
                f"\n**IMPORTANT: Skip these already-completed phases: "
                f"{', '.join(skipped)}**\n"
            )

    deploy_enabled = bool(config.fly_api_token)
    deploy_checkpoint_instructions = ""
    if deploy_enabled:
        deploy_checkpoint_instructions = (
            "\n### Deploy Checkpoint 1: Smoke Test (after scaffold)\n"
            "1. Create Fly app and provision infrastructure\n"
            "2. Run `flyctl deploy` and verify health endpoint returns 200\n"
            "3. If fails, send deploy logs to **fixer**, redeploy (max 3 attempts)\n"
            "4. Save the app name and URL — you will redeploy to the SAME app later\n\n"
        )

    deploy_midpoint = ""
    if deploy_enabled:
        deploy_midpoint = (
            "\n   After every 3 completed tasks, run a deploy checkpoint:\n"
            "   - `flyctl deploy --app <app-name>` (same app from Checkpoint 1)\n"
            "   - Verify health endpoint returns 200\n"
            "   - If fails, send deploy logs to **fixer**, redeploy (max 3)\n"
        )

    deploy_pre_review = ""
    if deploy_enabled:
        deploy_pre_review = (
            "\n### Deploy Checkpoint 3: Full Deploy (before review)\n"
            "1. `flyctl deploy --app <app-name>` (same app)\n"
            "2. Verify health check passes\n"
            "3. Note the deployed URL — the reviewer will run e2e tests against it\n\n"
        )

    db_note = ""
    if has_db:
        db_note = (
            "\nThis project has a database. The reviewer will include "
            "database schema and query review in its pass.\n"
        )

    return f"""\
You are the orchestrator for building a complete software project from a PRD.
You have 5 specialized subagents — delegate work to them and coordinate the build.

## Your Responsibilities

1. **Read and understand** the PRD below
2. **Delegate phases** to the appropriate subagents in order
3. **Verify outputs** using the verification scripts after each phase
4. **Course-correct** if verification fails — send specific issues to the **fixer**
5. **Track progress** — commit and push after each verified phase
6. **Never trust claims** — run `npm run build` and `npm test` YOURSELF after every subagent
{skip_instructions}
## Verification Scripts

After each phase, run the appropriate verification command and READ the output:
```bash
# Architecture verification
python -m src.orchestrator.verifier architecture

# Scaffold verification
python -m src.orchestrator.verifier scaffold

# Task verification (after each builder task)
python -m src.orchestrator.verifier task "<task_name>"

# Review verification
python -m src.orchestrator.verifier review

# LLM acceptance criteria check (after each builder task)
python -m src.orchestrator.llm_judge "<task_name>" "<acceptance_criteria>"
```
Each outputs JSON: {{"passed": true/false, "issues": [...]}}
If passed=false, send the issues to the **fixer** subagent, then re-verify.
The LLM judge outputs: {{"passed": true/false, "evidence": "...", "missing": [...]}}
If passed=false, send the missing items back to the **builder**.

## Pipeline Phases

### Phase 1: Architecture + Planning
1. Send the PRD to the **architect** subagent
   - It MUST produce ALL FOUR documents:
     a. docs/ARCHITECTURE.md — technical architecture
     b. docs/BUILD_PLAN.md — ordered build tasks
     c. docs/USER_FLOWS.md — E2E test flows for every feature
     d. docs/SEED_DATA.md — test data seeding manifest
2. Run architecture verification: `python -m src.orchestrator.verifier architecture`
3. If verification fails, provide the issues to **architect** and re-run
4. Read docs/BUILD_PLAN.md — verify tasks have acceptance criteria
5. **CRITICAL CHECK:** Verify docs/USER_FLOWS.md and docs/SEED_DATA.md exist.
   If either is missing, send the **architect** back with:
   "You are missing docs/USER_FLOWS.md and/or docs/SEED_DATA.md. These are MANDATORY.
   Generate them now following the format instructions in your prompt."
6. Commit: `git add -A && git commit -m "docs: add architecture and build plan"`
7. Push: `git push origin {branch_name}`

### Phase 2: Scaffold
1. Send to the **scaffolder** subagent
2. Run scaffold verification: `python -m src.orchestrator.verifier scaffold`
3. If fails, send issues to **fixer**, then re-verify (max 3 attempts)
4. Commit: `git add -A && git commit -m "chore: scaffold project structure"`
5. Push: `git push origin {branch_name}`
{deploy_checkpoint_instructions}\
### Phase 3: Build (implement each task)
Read docs/BUILD_PLAN.md and implement tasks in order:
1. For each task, send it to the **builder** subagent with:
   - Task number, name, description, acceptance criteria
   - List of already-completed tasks for context
   - Reminder: "Write a failing API integration test FIRST, then implement"
2. After the builder completes, verify:
   - Run `npm run build` — read the output, confirm exit 0
   - Run `npm test` — read the output, confirm 0 failures
   - Run task verification: `python -m src.orchestrator.verifier task "<task_name>"`
   - If ANY fails, send issues to **fixer**, re-verify (max 3 attempts per task)
   - Run LLM acceptance check:
     `python -m src.orchestrator.llm_judge "<task_name>" "<acceptance_criteria from BUILD_PLAN.md>"`
     Read the JSON output. If passed=false, the "missing" list tells you what's incomplete.
     Send missing items back to the **builder** with specific instructions.
3. Spot-check the builder's work:
   - Read the test file — does it test real behavior (not mocks)?
   - Read the implementation — is it wired to the DB and routes?
   - If you find TODO stubs or unwired code, send back to **builder** with specific feedback
4. Commit ONLY after verified: `git add -A && git commit -m "feat: <task name>"`
5. Push: `git push origin {branch_name}`
{deploy_midpoint}\

### Pre-Review: User Flow Audit
Before starting reviews, verify every key user flow yourself:
1. Read docs/ARCHITECTURE.md and list every user-facing flow
   (signup, login, create/read/update/delete for each resource, file upload, search, etc.)
2. For EACH flow, trace the full path through the code:
   - Frontend: is there a button/form/link that triggers it?
   - API call: does the frontend actually call the endpoint?
   - Backend: does the route handler do real work (not a stub/mock)?
   - Database: does it read/write real data (not hardcoded/mock)?
   - Response: does the result flow back to the UI?
3. Run: `grep -r "mock\\|Mock\\|hardcoded\\|TODO\\|FIXME\\|placeholder" src/`
   and investigate every hit.
4. For auth specifically: verify the REAL auth provider is configured,
   not anonymous/mock sign-in. Check for: signInAnonymously,
   mock-auth, fake-token, hardcoded-user, skip-auth.
5. Any broken flows → send to **fixer** with: "Flow X is broken because [specific gap]. Fix it."
6. Re-verify after fixes.
{deploy_pre_review}\
### Phase 4: Review & E2E Tests
1. Send to the **reviewer** subagent — it performs code quality review,
   security audit,{" database review," if has_db else ""} Playwright e2e browser tests,
   and writes CODE_REVIEW.md + PR_DESCRIPTION.md
{db_note}
2. Fix-verify loop (max 5 rounds):
   a. Collect all issues from the reviewer (code, security, e2e failures)
   b. Send critical issues to **fixer**
   c. Run `npm run build` — confirm exit 0
   d. Run `npm test` — confirm 0 failures
   e. Re-run **reviewer** on any pages/areas that previously failed
   f. If new issues found, repeat from (a)
   g. If build/tests/e2e all pass clean → exit loop
   h. After 5 rounds, document remaining issues in CODE_REVIEW.md and proceed

3. Run review verification: `python -m src.orchestrator.verifier review`
4. Commit: `git add -A && git commit -m "docs: add review results"`
5. Push: `git push origin {branch_name}`

### Phase 5: Finalize
1. Commit: `git add -A && git commit -m "docs: finalize"`
2. Push: `git push origin {branch_name}`
3. Create PR: `gh pr create --title "<descriptive title>" --body-file docs/PR_DESCRIPTION.md --base main --head {branch_name}`

**NOTE:** Deployment, test data seeding, and E2E testing are handled automatically
by the pipeline after the orchestrator completes. Do NOT deploy yourself in Phase 5.
The PR description should mention that E2E tests will be run post-deploy.

## Rules

- Run subagents **one at a time** — each phase depends on the previous
- After EVERY subagent, **run verification** and read the output — never skip this
- If verification fails, send specific issues to **fixer** and re-verify
- Maximum 3 retry attempts per verification failure before moving on
- Always commit and push after each verified phase
- The **reviewer** runs Playwright e2e against the deployed URL if available, otherwise localhost

## PRD

{prd_content}

Begin with Phase 1. Work through all phases in order.
"""


async def run_pipeline(
    prd_content: str,
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str,
    skip: dict[str, bool],
) -> dict | None:
    """Run the full build pipeline using an orchestrator agent with subagents.

    The orchestrator is a Claude agent that coordinates specialized subagents
    via the SDK's native AgentDefinition pattern.  Each subagent has focused
    tools and expertise.  The orchestrator decides when to invoke each one
    based on its description field.
    """
    # ── Initialize orchestrator components ────────────────────────────
    progress = ProgressTracker(repo_path, config.job_id)
    tech_profile = detect_tech_stack(repo_path)

    print(
        f"[runner] Tech profile: {tech_profile.languages}, "
        f"frontend={tech_profile.frontend_framework}, "
        f"backend={tech_profile.backend_framework}, "
        f"db={tech_profile.database}"
    )
    progress.update_tech_profile(asdict(tech_profile))

    vp_skill_path = f"{config.vp_script_path.rsplit('/scripts', 1)[0]}/SKILL.md"
    loader = ComponentLoader(
        config.claude_config_path, vp_skill_path, tech_profile
    )
    context_builder = ContextBuilder(repo_path)

    has_db = bool(tech_profile.database)

    # ── Build subagent definitions ────────────────────────────────────
    subagents = _build_subagents(
        loader, context_builder, config, tech_profile, repo_path, has_db
    )

    # ── Build orchestrator prompt ─────────────────────────────────────
    orchestrator_prompt = _build_orchestrator_prompt(
        prd_content, repo_path, config, branch_name, skip, has_db,
    )

    # ── Launch the orchestrator agent ─────────────────────────────────
    await reporter.report("orchestrator_started", {
        "tech_profile": asdict(tech_profile),
        "subagents": list(subagents.keys()),
    })

    progress.start_phase("orchestrator")

    try:
        result = await run_agent(
            prompt=orchestrator_prompt,
            system_prompt=(
                "You are a build orchestrator. You manage a team of 5 specialized "
                "subagents to build a complete software project from a PRD. "
                "Delegate work to subagents and verify their output.\n\n"
                "VERIFICATION RULE: Never trust a subagent's claim that something works. "
                "After every subagent completes:\n"
                "1. Run `npm run build` and `npm test` YOURSELF and read the actual output\n"
                "2. Run the appropriate verification script and read the JSON result\n"
                "3. If either shows failures, send the issues to the fixer before proceeding\n\n"
                "ACCEPTANCE CRITERIA RULE: After deterministic verification passes for a task, "
                "run the LLM judge to check acceptance criteria. If the judge says criteria are "
                "not met, send the specific missing items back to the builder. The judge is cheap "
                "(Haiku) — always run it.\n\n"
                "USER FLOW RULE: Before the review phase, trace every user-facing flow "
                "through the code. If any flow has a mock, stub, anonymous auth, or "
                "missing UI element — it is NOT complete. Send it to the fixer.\n\n"
                "Commit and push after each phase — but ONLY after verified green tests."
            ),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"],
            agents=subagents,
            cwd=repo_path,
            model="claude-opus-4-6",  # Orchestrator uses Opus for coordination judgment
            max_turns=200,
            reporter=reporter,
        )

        progress.record_agent_result(
            "orchestrator", result.cost_usd, result.turns
        )
        progress.complete_phase("orchestrator")
        progress.save()

        await reporter.report("orchestrator_complete", {
            "cost_usd": result.cost_usd,
            "turns": result.turns,
        })

    except Exception as exc:
        progress.fail_phase("orchestrator", str(exc))
        progress.save()
        raise

    # ── Ensure USER_FLOWS.md + SEED_DATA.md exist ────────────────────
    # If the architect/orchestrator didn't generate these, run a
    # dedicated agent to produce them so E2E testing can proceed.
    user_flows_path = Path(repo_path) / "docs" / "USER_FLOWS.md"
    seed_data_path = Path(repo_path) / "docs" / "SEED_DATA.md"
    missing_docs = []
    if not user_flows_path.exists():
        missing_docs.append("docs/USER_FLOWS.md")
    if not seed_data_path.exists():
        missing_docs.append("docs/SEED_DATA.md")

    if missing_docs:
        print(f"[runner] Missing E2E docs: {', '.join(missing_docs)} — generating...")
        await reporter.report("generating_test_docs", {"missing": missing_docs})
        try:
            arch_path = Path(repo_path) / "docs" / "ARCHITECTURE.md"
            arch_content = arch_path.read_text() if arch_path.exists() else ""
            prd_path = Path(repo_path) / "docs" / "PRD.md"
            prd_for_docs = prd_path.read_text() if prd_path.exists() else prd_content

            await run_agent(
                prompt=(
                    "You are an expert QA architect. Read the PRD and ARCHITECTURE.md below, "
                    "then generate the missing test documents.\n\n"
                    f"**Missing:** {', '.join(missing_docs)}\n\n"
                    f"## PRD\n\n{prd_for_docs}\n\n"
                    f"## Architecture\n\n{arch_content}\n\n"
                    + (user_flows_instructions() if not user_flows_path.exists() else "")
                    + (seed_data_instructions() if not seed_data_path.exists() else "")
                    + "\n\nWrite the missing file(s) now. Do NOT skip any user flows."
                ),
                allowed_tools=["Read", "Write", "Edit", "Grep", "Glob"],
                cwd=repo_path,
                model=config.model,
                max_turns=30,
                reporter=reporter,
            )
            git_commit(repo_path, "docs: add USER_FLOWS.md and SEED_DATA.md for E2E testing")
            if branch_name:
                git_push(repo_path, branch_name)
            print("[runner] Test docs generated successfully")
        except Exception as exc:
            print(f"[runner] Warning: failed to generate test docs: {exc}")

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
            # Save deploy info so retries can skip deployment and still have the URLs
            if deploy_result and deploy_result.get("fly_app_name"):
                progress.set_deploy_info(
                    deploy_result["fly_app_name"],
                    deploy_result.get("live_url", ""),
                )
            progress.complete_phase("deployment")
            progress.save()
        except Exception as exc:
            progress.fail_phase("deployment", str(exc))
            progress.save()
            raise

    elif skip.get("deployment"):
        # Deployment already done — load deploy info from PROGRESS.json
        print("[runner] Deployment skipped (already completed)")
        if progress.progress.deploy_app_name and progress.progress.deploy_url:
            deploy_result = {
                "live_url": progress.progress.deploy_url,
                "fly_app_name": progress.progress.deploy_app_name,
                "neon_project_id": None,
            }
            print(f"[runner] Loaded deploy info from PROGRESS.json: {deploy_result}")

    # ── Post-deploy: Seed test data ──────────────────────────────
    if deploy_result and deploy_result.get("neon_project_id") and not skip.get("seeding"):
        progress.start_phase("seeding")
        try:
            from src.pipeline.seeder import seed_test_data

            creds_file = Path("/tmp/neon-credentials.json")
            db_url = None
            if creds_file.exists():
                db_url = json.loads(creds_file.read_text()).get("database_url")

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

    if live_url and fly_app_name and not skip.get("e2e_testing"):
        progress.start_phase("e2e_testing")
        try:
            from src.pipeline.e2e_loop import run_e2e_loop
            import os
            import subprocess

            # Set RESEND_API_KEY as fly secret for the app (non-fatal)
            if config.resend_api_key:
                os.environ.setdefault("FLY_API_TOKEN", config.fly_api_token)
                try:
                    subprocess.run(
                        ["flyctl", "secrets", "set",
                         f"RESEND_API_KEY={config.resend_api_key}",
                         "-a", fly_app_name],
                        capture_output=True, text=True, timeout=120,
                    )
                except Exception as e:
                    print(f"[runner] Warning: failed to set RESEND_API_KEY secret: {e}")

            test_report = await run_e2e_loop(
                repo_path=repo_path,
                app_url=live_url,
                fly_app_name=fly_app_name,
                config=config,
                reporter=reporter,
                branch_name=branch_name,
                timeout_hours=5,
                cost_limit_usd=150.0,
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

    return deploy_result
