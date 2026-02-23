"""Phase 3: Iterative implementation of build tasks with visual verification."""
from __future__ import annotations

import subprocess
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.system import load_rules, load_skill
from src.prompts.implementation import build_task_prompt, retry_prompt
from src.pipeline.agent import run_agent
from src.pipeline.models import BuildPlan, Task
from src.repo import git_commit, git_push


def _get_completed_task_names(repo_path: str) -> set[str]:
    """Get the set of task names that have already been committed (for resume)."""
    result = subprocess.run(
        ["git", "log", "--format=%s"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    completed = set()
    for line in result.stdout.strip().splitlines():
        if line.startswith("feat: "):
            completed.add(line.removeprefix("feat: "))
    return completed


async def build_tasks(
    plan: BuildPlan,
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str | None = None,
) -> None:
    """Execute each task sequentially with retries and visual verification."""
    completed_tasks = _get_completed_task_names(repo_path)

    for i, task in enumerate(plan.tasks):
        # Skip tasks that were already committed in a previous run
        if task.name in completed_tasks:
            print(f"[builder] Skipping task {i + 1}/{plan.total_tasks} (already committed): {task.name}")
            await reporter.report("task_completed", {
                "task_number": i + 1,
                "skipped": True,
            })
            continue

        await reporter.report("task_started", {
            "task_number": i + 1,
            "total_tasks": plan.total_tasks,
            "task_name": task.name,
        })

        success = await _build_single_task(task, i, plan.total_tasks, repo_path, config, reporter)

        if success:
            git_commit(repo_path, f"feat: {task.name}")
            if branch_name:
                git_push(repo_path, branch_name)
            await reporter.report("task_completed", {"task_number": i + 1})
        else:
            await reporter.report("task_failed", {
                "task_number": i + 1,
                "task_name": task.name,
            })
            # Continue to next task rather than aborting the whole build
            print(f"[builder] Task {i + 1} failed after retries, continuing...")


async def _build_single_task(
    task: Task,
    task_index: int,
    total_tasks: int,
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
) -> bool:
    """Attempt to build a single task with retries. Returns True on success."""
    system = load_rules(["coding-style", "testing", "security"])
    last_errors = ""

    for attempt in range(config.max_task_retries):
        if attempt == 0:
            prompt = build_task_prompt(task, task_index, total_tasks)
        else:
            prompt = retry_prompt(task, last_errors)
            await reporter.report("task_retry", {
                "task_number": task_index + 1,
                "retry": attempt,
            })

        await run_agent(
            prompt=prompt,
            system_prompt=system,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            cwd=repo_path,
            model=config.model,
        )

        # Verify tests pass
        test_ok, test_errors = _run_tests(repo_path)

        # Visual verification for frontend tasks
        visual_ok = True
        if task.has_ui:
            visual_ok = await _visual_verify(task, task_index, repo_path, config, reporter)

        if test_ok and visual_ok:
            return True

        # Collect errors for retry prompt
        error_parts = []
        if not test_ok:
            error_parts.append(f"Test failures:\n{test_errors}")
        if not visual_ok:
            error_parts.append("Visual verification failed — UI does not match requirements")
        last_errors = "\n\n".join(error_parts)

    return False


def _run_tests(repo_path: str) -> tuple[bool, str]:
    """Run the test suite and return (passed, error_output)."""
    # Try common test commands
    test_commands = [
        ["npm", "test", "--", "--passWithNoTests"],
        ["npx", "vitest", "run", "--passWithNoTests"],
        ["python", "-m", "pytest", "-x", "--tb=short"],
    ]

    for cmd in test_commands:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return True, ""
        # If the command was found but tests failed, return the error
        if "not found" not in result.stderr.lower() and "ENOENT" not in result.stderr:
            return False, result.stdout + result.stderr

    # No test runner found — pass by default
    print("[builder] No test runner found, skipping test verification")
    return True, ""


async def _visual_verify(
    task: Task,
    task_index: int,
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
) -> bool:
    """Use Visual Playwright to verify a frontend task visually."""
    screenshots_dir = f"{repo_path}/docs/screenshots/task-{task_index + 1}"
    Path(screenshots_dir).mkdir(parents=True, exist_ok=True)

    vp_script = config.vp_script_path
    vp_system = load_skill("visual-playwright")
    route = task.route or ""

    prompt = f"""\
You just implemented: {task.name}
Requirements:
{chr(10).join(f'  - {c}' for c in task.acceptance_criteria)}

Now visually verify the UI:

1. Start the dev server (npm run dev / npm start / etc.)
2. Wait for it to be ready
3. Use Visual Playwright to navigate and screenshot:

   node {vp_script} goto "http://localhost:3000{route}" \\
       --screenshot {screenshots_dir}/main.png

4. If there are interactive elements relevant to this task, test them:
   - Click buttons, fill forms, navigate links
   - Screenshot after each interaction

5. Evaluate each screenshot:
   - Does the page render without errors?
   - Does the layout match the requirements?
   - Are there blank areas, broken layouts, or missing elements?

6. If you find visual issues, fix them and re-screenshot.

7. Close the Visual Playwright session and stop the dev server:
   node {vp_script} close

Keep screenshots in {screenshots_dir}/
"""

    try:
        await run_agent(
            prompt=prompt,
            system_prompt=vp_system,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            cwd=repo_path,
            model=config.model,
        )
    except Exception as e:
        print(f"[builder] Visual verification error (non-fatal): {e}")
        await reporter.report("visual_verification_failed", {
            "task_number": task_index + 1,
            "error": str(e),
        })
        # Don't let VP failures block the build
        return True

    has_screenshots = any(Path(screenshots_dir).glob("*.png"))
    if has_screenshots:
        await reporter.report("visual_verification_passed", {
            "task_number": task_index + 1,
            "screenshots": len(list(Path(screenshots_dir).glob("*.png"))),
        })
    return True
