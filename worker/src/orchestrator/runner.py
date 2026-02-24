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
from src.pipeline.models import parse_build_plan
from src.repo import git_commit, git_push


def _build_subagents(
    loader: ComponentLoader,
    config: Config,
    tech_profile: TechProfile,
    repo_path: str,
    has_db: bool,
) -> dict[str, AgentDefinition]:
    """Build the AgentDefinition map for the orchestrator's subagents."""

    vp_script = config.vp_script_path
    screenshots_base = f"{repo_path}/docs/screenshots"

    agents: dict[str, AgentDefinition] = {}

    # ── Architect ──────────────────────────────────────────────────────
    agents["architect"] = AgentDefinition(
        description=(
            "System architect. Use this agent to design the technical "
            "architecture from a PRD — tech stack, data models, API "
            "contracts, directory structure. It writes docs/ARCHITECTURE.md."
        ),
        prompt=(
            "You are an expert software architect. Read the PRD provided "
            "and produce a comprehensive architecture document.\n\n"
            "Decide on:\n"
            "- Tech stack (languages, frameworks, databases)\n"
            "- Directory structure\n"
            "- Major components and responsibilities\n"
            "- Data models and schemas (ALL tables, ALL columns, ALL relations)\n"
            "- API contracts (every endpoint, request/response shapes)\n"
            "- Authentication and authorization approach\n"
            "- Error handling strategy\n\n"
            "Write the architecture doc to docs/ARCHITECTURE.md."
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Planner ────────────────────────────────────────────────────────
    agents["planner"] = AgentDefinition(
        description=(
            "Task planner. Use this agent to decompose the architecture "
            "into an ordered list of implementation tasks. It reads "
            "docs/ARCHITECTURE.md and writes docs/BUILD_PLAN.md."
        ),
        prompt=(
            "You are a project planner. Read docs/ARCHITECTURE.md and "
            "docs/PRD.md, then break the implementation into 8-15 ordered "
            "tasks.\n\n"
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
            "Write the plan to docs/BUILD_PLAN.md."
        ),
        tools=["Read", "Write"],
        model="sonnet",
    )

    # ── Scaffolder ─────────────────────────────────────────────────────
    scaffolder_system = loader.for_scaffolder()
    agents["scaffolder"] = AgentDefinition(
        description=(
            "Project scaffolder. Use this agent to create the full project "
            "skeleton — directory structure, package.json with ALL deps, "
            "configs, database schema, route stubs, shared types, test infra. "
            "It must build clean with zero errors."
        ),
        prompt=(
            f"{scaffolder_system}\n\n" if scaffolder_system else ""
        ) + (
            "Create the full project scaffold based on docs/ARCHITECTURE.md "
            "and docs/BUILD_PLAN.md.\n\n"
            "1. Directory structure matching the architecture exactly\n"
            "2. Package manager config with ALL dependencies for the full build\n"
            "3. Language configs (tsconfig.json, .eslintrc, etc.)\n"
            "4. Database schema/ORM models — all tables, all columns, all relations\n"
            "5. API route stubs with correct paths, methods, parameter types\n"
            "6. Shared types/interfaces\n"
            "7. Test infrastructure\n"
            "8. .env.example with all required vars\n\n"
            "Rules:\n"
            "- Install ALL deps and verify `npm run build` (or equivalent) succeeds\n"
            "- No TODO comments — use minimal valid implementations instead\n"
            "- Define real data models with all fields, not just id and name"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Builder (feature implementer) ──────────────────────────────────
    builder_system = loader.for_builder()
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
            "You are a feature implementer. When given a task:\n\n"
            "1. Read docs/PRD.md, docs/ARCHITECTURE.md, and docs/BUILD_PLAN.md\n"
            "2. Read the existing source files you'll modify\n"
            "3. FULLY IMPLEMENT every function, component, and route\n"
            "4. Wire everything together — API to DB, UI to API, imports to usage\n"
            "5. Follow existing code patterns and conventions\n"
            "6. Handle error cases, loading states, edge cases\n"
            "7. Run `npm run build` and fix any errors\n\n"
            "CRITICAL RULES:\n"
            "- No placeholder returns, no // TODO, no pass stubs\n"
            "- Every piece of code must be complete and functional\n"
            "- Use dependencies already in package.json\n"
            "- Search your own code for TODO/FIXME/placeholder and replace with real code"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Build Error Resolver ───────────────────────────────────────────
    resolver_system = loader.for_build_error_resolver()
    agents["build-error-resolver"] = AgentDefinition(
        description=(
            "Build error resolver. Use this agent when builds or tests fail. "
            "Give it the error output and it will diagnose and fix the issues."
        ),
        prompt=(
            f"{resolver_system}\n\n" if resolver_system else ""
        ) + (
            "You fix build and test failures. When given error output:\n"
            "1. Identify every distinct error\n"
            "2. Read the relevant source files\n"
            "3. Fix ALL issues, not just the first one\n"
            "4. Verify the fix by running the build/test again\n"
            "5. Check for TODO/FIXME/placeholder code and replace with real code"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Code Reviewer ──────────────────────────────────────────────────
    reviewer_system = loader.for_reviewer()
    agents["code-reviewer"] = AgentDefinition(
        description=(
            "Code reviewer. Use this agent to review the entire codebase "
            "for quality, error handling, test coverage, and performance. "
            "It writes docs/CODE_REVIEW.md and fixes critical issues directly."
        ),
        prompt=(
            f"{reviewer_system}\n\n" if reviewer_system else ""
        ) + (
            "Review the entire codebase for:\n"
            "1. Code quality and maintainability\n"
            "2. Error handling completeness\n"
            "3. Test coverage gaps\n"
            "4. Performance concerns\n\n"
            "Write your review to docs/CODE_REVIEW.md with file:line references.\n"
            "Fix critical issues directly. Document minor issues in the review."
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Security Reviewer ──────────────────────────────────────────────
    security_system = loader.for_security_reviewer()
    agents["security-reviewer"] = AgentDefinition(
        description=(
            "Security reviewer. Use this agent to audit the codebase for "
            "security vulnerabilities — injection, auth issues, hardcoded "
            "secrets, insecure configs, dependency vulns."
        ),
        prompt=(
            f"{security_system}\n\n" if security_system else ""
        ) + (
            "Perform a security review. Check for:\n"
            "- Hardcoded secrets or credentials\n"
            "- Injection vulnerabilities (SQL, command, XSS)\n"
            "- Authentication and authorization issues\n"
            "- Dependency vulnerabilities (run npm audit if applicable)\n"
            "- Insecure configurations\n"
            "- Missing input validation\n\n"
            "Append findings to docs/CODE_REVIEW.md under '## Security Review'.\n"
            "Fix critical security issues directly."
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Database Reviewer (conditional) ────────────────────────────────
    if tech_profile.needs_db_reviewer:
        db_system = loader.for_db_reviewer()
        agents["database-reviewer"] = AgentDefinition(
            description=(
                "Database reviewer. Use this agent to review database schema, "
                "queries, and migrations for correctness, performance, and "
                "security. Only use when the project has a database."
            ),
            prompt=(
                f"{db_system}\n\n" if db_system else ""
            ) + (
                "Review the database layer:\n"
                "- Schema design (normalization, indexes, constraints)\n"
                "- Query patterns (N+1 problems, missing indexes)\n"
                "- Migration safety\n"
                "- Connection pooling and error handling\n\n"
                "Append findings to docs/CODE_REVIEW.md under '## Database Review'.\n"
                "Fix critical issues directly."
            ),
            tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            model="sonnet",
        )

    # ── Visual E2E Runner ──────────────────────────────────────────────
    e2e_system = loader.for_e2e_runner()
    agents["visual-e2e"] = AgentDefinition(
        description=(
            "Visual E2E tester. Use this agent to start the dev server, "
            "navigate every page with Visual Playwright, take screenshots, "
            "verify the UI renders correctly, and fix visual issues."
        ),
        prompt=(
            f"{e2e_system}\n\n" if e2e_system else ""
        ) + (
            f"Perform a full visual E2E walkthrough:\n"
            f"1. Read docs/PRD.md and docs/ARCHITECTURE.md for routes\n"
            f"2. Start the dev server\n"
            f"3. Use Visual Playwright to visit every page:\n"
            f"   node {vp_script} goto \"http://localhost:3000\" "
            f"--screenshot {screenshots_base}/e2e/home.png\n"
            f"4. Test key user flows (auth, CRUD, forms)\n"
            f"5. Fix visual issues found\n"
            f"6. Write docs/VISUAL_REVIEW.md with pass/fail per page\n"
            f"7. Close sessions: node {vp_script} close\n"
            f"Keep screenshots in {screenshots_base}/e2e/"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── PR Description Writer ──────────────────────────────────────────
    agents["pr-writer"] = AgentDefinition(
        description=(
            "PR description writer. Use this agent to generate a "
            "comprehensive pull request description. It reads all docs "
            "and writes docs/PR_DESCRIPTION.md."
        ),
        prompt=(
            "Generate a comprehensive PR description:\n"
            "1. Summarize what was built (1-2 paragraphs)\n"
            "2. Map PRD requirements to implementing code\n"
            "3. List architectural decisions\n"
            "4. Note deferred items or limitations\n"
            "5. Include test coverage stats\n"
            "6. Reference screenshots from docs/screenshots/\n\n"
            "Read docs/PRD.md, ARCHITECTURE.md, BUILD_PLAN.md, CODE_REVIEW.md.\n"
            "Write to docs/PR_DESCRIPTION.md."
        ),
        tools=["Read", "Write", "Bash", "Grep", "Glob"],
        model="sonnet",
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

    deploy_instructions = ""
    if config.fly_api_token and not skip.get("deployment"):
        deploy_instructions = (
            "\n## Phase 6: Deploy\n"
            "After finalizing, deploy the project. This is handled by "
            "existing deployment code — just ensure the build is production-ready.\n"
        )

    db_note = ""
    if has_db:
        db_note = (
            "\nThis project has a database. Use the database-reviewer "
            "subagent during the review phase.\n"
        )

    return f"""\
You are the orchestrator for building a complete software project from a PRD.
You have specialized subagents available — delegate work to them and coordinate
the overall build process.

## Your Responsibilities

1. **Read and understand** the PRD below
2. **Delegate phases** to the appropriate subagents in order
3. **Verify outputs** after each subagent completes — read the files they created
4. **Course-correct** if a subagent produces poor output — provide guidance and re-run
5. **Track progress** — after each phase, commit artifacts with git
6. **Coordinate the full pipeline** from architecture through to a working, tested app
{skip_instructions}
## Pipeline Phases

### Phase 1: Planning
1. Send the PRD to the **architect** subagent to design the system
2. Read docs/ARCHITECTURE.md to verify it's comprehensive
3. Send to the **planner** subagent to decompose into tasks
4. Read docs/BUILD_PLAN.md to verify tasks are well-defined
5. Commit: `git add -A && git commit -m "docs: add architecture and build plan"`
6. Push: `git push origin {branch_name}`

### Phase 2: Scaffold
1. Send to the **scaffolder** subagent to create the project skeleton
2. Verify the build works: run `npm run build` (or equivalent)
3. If build fails, use **build-error-resolver** to fix
4. Commit: `git add -A && git commit -m "chore: scaffold project structure"`
5. Push: `git push origin {branch_name}`

### Phase 3: Build (implement each task)
Read docs/BUILD_PLAN.md and implement tasks in order:
1. For each task, send it to the **builder** subagent with:
   - Task number, name, description, acceptance criteria
   - List of already-completed tasks for context
2. After each task, run `npm run build` to verify
3. If build fails, use **build-error-resolver** to fix
4. Commit each completed task: `git add -A && git commit -m "feat: <task name>"`
5. Push after each task: `git push origin {branch_name}`

### Phase 4: Review
1. Use **code-reviewer** to review the full codebase
2. Use **security-reviewer** for security audit
{db_note}3. Use **visual-e2e** to screenshot and verify all pages
4. Fix any critical issues found
5. Commit: `git add -A && git commit -m "docs: add review results"`
6. Push: `git push origin {branch_name}`

### Phase 5: Finalize
1. Use **pr-writer** to generate the PR description
2. Commit: `git add -A && git commit -m "docs: add PR description"`
3. Push: `git push origin {branch_name}`
4. Create a GitHub PR: `gh pr create --title "<descriptive title>" --body-file docs/PR_DESCRIPTION.md --base main --head {branch_name}`
{deploy_instructions}
## Rules

- Run subagents **one at a time** — each phase depends on the previous
- After each subagent completes, **read the key output files** to verify quality
- If a subagent's output is poor (missing features, TODO stubs, broken build),
  provide specific feedback and re-run with guidance
- Always commit and push after completing each phase
- If `npm run build` fails after a subagent, use **build-error-resolver** before proceeding

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

    loader = ComponentLoader(
        config.claude_config_path, config.vp_skill_path, tech_profile
    )

    has_db = bool(tech_profile.database)

    # ── Build subagent definitions ────────────────────────────────────
    subagents = _build_subagents(loader, config, tech_profile, repo_path, has_db)

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
                "You are a build orchestrator. You manage a team of specialized "
                "subagents to build a complete software project from a PRD. "
                "Delegate work to subagents and verify their output. "
                "Commit and push after each phase."
            ),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"],
            agents=subagents,
            cwd=repo_path,
            model=config.model,
            max_turns=200,  # orchestrator needs many turns to coordinate full build
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

    elif skip.get("deployment"):
        progress.skip_phase("deployment")

    return deploy_result
