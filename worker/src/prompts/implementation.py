from __future__ import annotations

from src.pipeline.models import Task


def build_task_prompt(task: Task, task_index: int, total_tasks: int) -> str:
    deps_str = ", ".join(task.dependencies) if task.dependencies else "None"
    files_str = ", ".join(task.target_files) if task.target_files else "As needed"
    criteria_str = "\n".join(f"  - {c}" for c in task.acceptance_criteria)

    return f"""\
Implement task {task_index + 1}/{total_tasks}: {task.name}

Description: {task.description}

Files to create/modify: {files_str}
Dependencies (already built): {deps_str}

Acceptance Criteria:
{criteria_str}

After implementing:
1. Write unit tests for the new code
2. Run the full test suite
3. Fix any failures before declaring the task complete
"""


def retry_prompt(task: Task, errors: str) -> str:
    return f"""\
The previous implementation attempt for "{task.name}" had issues:

{errors}

Please fix these issues. Run the test suite again after fixing.
"""


def scaffold_prompt() -> str:
    return """\
Based on the architecture at docs/ARCHITECTURE.md and the build plan \
at docs/BUILD_PLAN.md, create the full project scaffold:

1. Directory structure matching the architecture
2. Package manager config with all dependencies (package.json / pyproject.toml / etc.)
3. Language config (tsconfig.json / .eslintrc / pyproject.toml sections / etc.)
4. Database schema or type definitions (if applicable)
5. Test infrastructure (vitest / jest / pytest / etc.)
6. A basic CI workflow (.github/workflows/ci.yml)
7. README with setup instructions
8. Any environment config (.env.example, docker-compose for local services, etc.)

Install dependencies and verify the project builds clean with no errors.
"""
