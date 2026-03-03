# Pipeline Redesign: Hybrid SDK Orchestrator + Python Guardrails

**Date:** 2026-03-03
**Status:** Approved design, pending implementation

## Problem Statement

The current pipeline has three operational problems:

1. **Output quality** — Features get built in isolation but aren't wired together (API exists but frontend doesn't call it, DB schema exists but routes don't query it). Agents use mock/anonymous auth instead of real implementations. Apps work locally but break when deployed.

2. **Reliability** — Bad scaffolds cascade into every subsequent builder task. Error resolver loops burn tokens fixing symptoms of structural problems without converging.

3. **Cost** — Token waste from error resolver loops fighting bad foundations. No bounded retries — the LLM decides when to stop retrying.

**Root cause chain:** Bad scaffold → every builder fights it → error resolver loops on symptoms → burns tokens → broken output or turn limit hit.

**Success criteria:** 90%+ of builds produce working, deployed apps with all user flows functional.

## Approach: Hybrid SDK Orchestrator + Python Guardrails

Keep the Claude Agent SDK orchestrator (adaptive intelligence for coordination) but add:
- Python verification functions that gate phase transitions
- Structural wiring checks (not just `npm run build`)
- Deploy checkpoints throughout the pipeline (not just at the end)
- Consolidated subagents (11 → 5)
- Model hierarchy (Opus for judgment, Sonnet for execution, Haiku for inference)

### Why not a full Python state machine?

The SDK orchestrator provides adaptive cross-phase reasoning that a state machine can't:
- "The architect missed auth, so I'll tell the scaffolder to prioritize it"
- Handling novel situations (weird tech stacks, conflicting PRD requirements)
- Natural context flow between phases

The problems (bad scaffolds, no verification, error loops) aren't caused by the SDK pattern — they're caused by missing verification gates and unbounded retries. We fix those with Python guardrails while keeping the orchestrator's intelligence.

## Design

### 1. Consolidated Subagents (11 → 5)

| Agent | Absorbs | Model | Rationale |
|-------|---------|-------|-----------|
| **architect** | + planner | Opus | Produces ARCHITECTURE.md + BUILD_PLAN.md in one session. Architecture decisions cascade — worth the premium. |
| **scaffolder** | (unchanged) | Sonnet | Execution-heavy file generation. Architecture already tells it what to build. |
| **builder** | (unchanged) | Sonnet | Implements specific tasks with specific acceptance criteria. Well-scoped. |
| **fixer** | build-error-resolver | Sonnet | Handles all failures: build, test, deploy, review findings. Systematic debugging. |
| **reviewer** | code-reviewer + security-reviewer + database-reviewer + visual-e2e + pr-writer | Opus | One pass across entire codebase: quality + security + DB + e2e + PR description. Needs holistic reasoning. |

**Removed agents:**
- **planner** → absorbed into architect (same context, two output files)
- **evaluator** → becomes Python verification function (shell commands + file checks, no LLM needed)
- **code-reviewer, security-reviewer, database-reviewer** → merged into single reviewer
- **visual-e2e** → merged into reviewer (Playwright is part of the review pass)
- **pr-writer** → merged into reviewer (writes PR_DESCRIPTION.md as final output)

**New addition:**
- **LLM-as-Judge** (Haiku) — not a subagent, but a cheap model call after each builder task to check acceptance criteria satisfaction. Binary YES/NO with evidence.

### 2. Model Hierarchy

```
Opus:    Orchestrator, Architect, Reviewer
Sonnet:  Scaffolder, Builder, Fixer
Haiku:   LLM-as-Judge (acceptance criteria checks)
Python:  All verification scripts (no model)
```

### 3. Verification Gates

Every phase produces concrete artifacts. A Python verification function checks them before the pipeline advances. No phase completes on the agent's word alone.

#### New module: `worker/src/orchestrator/verifier.py`

```python
class PhaseVerifier:
    def verify_architecture(self) -> VerifyResult
    def verify_scaffold(self) -> VerifyResult
    def verify_task(self, task) -> VerifyResult
    def verify_review(self) -> VerifyResult

class StructuralChecker:
    def check_route_wiring(self) -> list[str]
    def check_model_wiring(self) -> list[str]
    def check_page_wiring(self) -> list[str]
    def check_no_stubs(self) -> list[str]
```

#### Per-phase verification:

**Architect → `docs/ARCHITECTURE.md` + `docs/BUILD_PLAN.md`**
- Files exist and non-empty
- Required sections present: Data Models, API Endpoints, Routes/Pages
- Every API endpoint has method, path, request shape, response shape
- Every data model has fields (not just a name)
- Build plan tasks have acceptance criteria

**Scaffolder → working project skeleton**
- `npm install` exits 0
- `npm run build` exits 0
- `npm test` exits 0 (health check test passes)
- Structural wiring: routes in ARCHITECTURE.md → route files in src
- Structural wiring: models in ARCHITECTURE.md → model/schema files in src
- Structural wiring: pages in ARCHITECTURE.md → component files in src
- No TODO/FIXME/placeholder in source files

**Builder (per task) → implemented feature with passing tests**
- `npm run build` exits 0
- `npm test` exits 0
- New test file exists for this task
- Test file contains real assertions (grep for expect/assert/toBe)
- No mock/placeholder/TODO in changed files
- LLM-as-Judge (Haiku): "Does this diff satisfy these acceptance criteria? YES/NO with evidence."

**Reviewer → review docs + passing e2e**
- CODE_REVIEW.md exists and non-empty
- PR_DESCRIPTION.md exists and non-empty
- Playwright tests passed
- Screenshots in docs/screenshots/e2e/
- No unfixed CRITICAL/HIGH issues in CODE_REVIEW.md

### 4. Deploy Checkpoints

Three deploy checkpoints throughout the pipeline, all to the **same Fly app** (no new servers per checkpoint).

**Checkpoint 1 — After scaffold (smoke test)**
- Create Fly app: `sod-{job_id[:8]}`
- Provision Neon DB (if needed) — one-time setup
- Set secrets — one-time setup
- `flyctl deploy`
- Verify: health endpoint returns 200
- Save app_name + deploy_url to PROGRESS.json
- If fails → fixer gets deploy logs, fix, redeploy (max 3 attempts)

**Checkpoint 2 — After every ~3 tasks (midpoint)**
- `flyctl deploy` (same app, same machine, ~60-90 second redeploy)
- Verify: app starts, DB connects, at least one API endpoint responds
- Catches: DB connection issues, env var problems, CORS, middleware ordering
- If fails → fixer, redeploy (max 3 attempts)

**Checkpoint 3 — After all tasks, before review**
- `flyctl deploy` (same app)
- Full health check
- Playwright e2e runs against **deployed URL** (not localhost)
- This is where user flow audit happens against the real app

#### New deployer function: `deploy_checkpoint()`

```python
async def deploy_checkpoint(repo_path, config, app_name, checkpoint_name):
    """Lightweight redeploy to existing Fly app + health check."""
    # flyctl deploy (reuses existing app + machine)
    # Hit health endpoint
    # Return pass/fail + logs

async def deploy_full(repo_path, config, reporter):
    """First-time: create app, provision Neon, set secrets, deploy."""
    # Only called at Checkpoint 1
```

### 5. User Flow Audit (Pre-Review)

After all build tasks complete and before the review phase, the orchestrator traces every user-facing flow through the code:

1. Read ARCHITECTURE.md, list every user flow (signup, login, CRUD per resource, file upload, search, etc.)
2. For each flow, trace the full path:
   - Frontend: button/form/link that triggers it?
   - API call: frontend actually calls the endpoint?
   - Backend: route handler does real work (not stub/mock)?
   - Database: reads/writes real data (not hardcoded)?
   - Response: result flows back to UI?
3. Automated grep for: `mock`, `Mock`, `hardcoded`, `TODO`, `FIXME`, `placeholder`, `signInAnonymously`, `mock-auth`, `fake-token`, `hardcoded-user`, `skip-auth`
4. Broken flows → fixer with specific gap description
5. Re-verify after fixes

### 6. Complete Pipeline Flow

```
run_pipeline()
│
├─ Python: load PROGRESS.json, determine skip map
├─ Python: detect tech stack, build 5 subagent definitions
│
└─► run_agent(orchestrator, agents={5}, model=opus, max_turns=200)
    │
    │  PHASE 1: ARCHITECTURE + PLANNING
    ├─ architect subagent (opus) → ARCHITECTURE.md + BUILD_PLAN.md
    ├─ Python verify → structure, sections, completeness
    ├─ If fail → re-run architect with gaps (max 3)
    ├─ Commit + push
    │
    │  PHASE 2: SCAFFOLD
    ├─ scaffolder subagent (sonnet) → project skeleton + test infra
    ├─ Python verify → build, test, structural wiring, no stubs
    ├─ If fail → fixer (sonnet), re-verify (max 3)
    ├─ Commit + push
    │
    │  DEPLOY CHECKPOINT 1: Smoke Test
    ├─ Create Fly app + provision Neon + set secrets
    ├─ flyctl deploy → health check 200
    ├─ If fail → fixer, redeploy (max 3)
    │
    │  PHASE 3: BUILD (per-task loop)
    ├─ For each task:
    │   ├─ builder subagent (sonnet) → TDD: failing test → implement → green
    │   ├─ Python verify → build, test, new test file, real assertions, no stubs
    │   ├─ LLM-as-Judge (haiku) → acceptance criteria check
    │   ├─ If fail → fixer (sonnet), re-verify (max 3)
    │   ├─ Commit + push
    │   └─ Every 3 tasks: DEPLOY CHECKPOINT 2 (redeploy, health check)
    │
    │  PRE-REVIEW: User Flow Audit
    ├─ Trace every user flow: UI → API → handler → DB → response
    ├─ Grep for mock/anonymous/hardcoded patterns
    ├─ Broken flows → fixer
    │
    │  DEPLOY CHECKPOINT 3: Full Deploy
    ├─ flyctl deploy (same app)
    │
    │  PHASE 4: REVIEW & E2E
    ├─ reviewer subagent (opus) → code + security + DB + Playwright e2e
    │   (e2e runs against DEPLOYED URL, not localhost)
    │   → CODE_REVIEW.md + PR_DESCRIPTION.md + screenshots
    ├─ Fix-verify loop (max 5 rounds):
    │   ├─ Collect all issues
    │   ├─ fixer (sonnet) addresses issues
    │   ├─ npm build + npm test
    │   ├─ Redeploy (same app)
    │   ├─ Re-run failing e2e against deployed URL
    │   └─ Clean → exit loop
    ├─ Python verify → review docs, e2e passed, no unfixed criticals
    ├─ Commit + push
    │
    │  PHASE 5: FINALIZE
    ├─ Final flyctl deploy
    ├─ gh pr create --body-file docs/PR_DESCRIPTION.md
    └─ Commit + push
```

## Files to Create/Modify

| File | Action | Changes |
|------|--------|---------|
| `worker/src/orchestrator/runner.py` | Modify | Consolidate 11 → 5 subagents, new orchestrator prompt with all phases + verification + deploy checkpoints + user flow audit, model hierarchy |
| `worker/src/orchestrator/verifier.py` | Create | PhaseVerifier + StructuralChecker classes |
| `worker/src/pipeline/deployer.py` | Modify | Add `deploy_checkpoint()` function for lightweight redeploys |
| `worker/src/orchestrator/progress.py` | Modify | Track deploy checkpoint state, app_name, deploy_url |

## Landscape Context

This design draws from patterns proven in the field:
- **Gas Town** (Steve Yegge): State in git/files, not context windows → our PROGRESS.json + verification scripts
- **Composio Agent Orchestrator**: CI-integrated verification → our Python verification gates
- **Nx Cloud Self-Healing CI**: Fix → re-run → verify loop → our Phase 4 fix-verify loop
- **MetaGPT**: Structured intermediate artifacts between phases → our per-phase deliverables
- **Vercel Ralph Loop Agent**: verifyCompletion callbacks → our Python verification functions

Key insight from the field: "If your agents are consistently underperforming, the issue likely isn't the wording of the instruction; it's the architecture of the collaboration."
