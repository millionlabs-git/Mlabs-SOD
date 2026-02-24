from __future__ import annotations

from src.pipeline.models import Task


def build_task_prompt(
    task: Task,
    task_index: int,
    total_tasks: int,
    completed_tasks: list[str] | None = None,
) -> str:
    deps_str = ", ".join(task.dependencies) if task.dependencies else "None"
    files_str = ", ".join(task.target_files) if task.target_files else "As needed"
    criteria_str = "\n".join(f"  - {c}" for c in task.acceptance_criteria)

    completed_context = ""
    if completed_tasks:
        completed_list = "\n".join(f"  - {t}" for t in completed_tasks)
        completed_context = f"""
## Already completed tasks:
{completed_list}
"""

    return f"""\
Implement task {task_index + 1}/{total_tasks}: **{task.name}**

## Context — read these FIRST before writing any code

1. Read `docs/PRD.md` to understand the product requirements
2. Read `docs/ARCHITECTURE.md` to understand the tech stack, data models, and API contracts
3. Read `docs/BUILD_PLAN.md` to understand how this task fits into the overall build
4. Read the existing source files (especially files you will modify) to understand \
current patterns, imports, and conventions already established
{completed_context}
## Task details

**Description:** {task.description}
**Files to create/modify:** {files_str}
**Dependencies (already built):** {deps_str}

**Acceptance Criteria:**
{criteria_str}

## Implementation rules — CRITICAL

- **FULLY IMPLEMENT every function, component, and route.** No placeholder returns, \
no `// TODO` comments, no `pass` stubs, no "implement later" notes. Every piece of \
code you write must be complete and functional.
- **Wire everything together.** If you create an API endpoint, connect it to the \
database. If you create a UI component, connect it to real data via API calls or \
state management. If you create a utility, import and use it where needed.
- **Follow existing patterns.** Match the code style, file structure, import patterns, \
and naming conventions already established in the codebase. Read existing files first.
- **Handle real-world cases.** Include proper error handling, loading states, empty \
states, and edge cases. Components should handle when data is missing or API calls fail.
- **Use the actual dependencies** already installed in package.json / requirements.txt. \
Don't invent new patterns — use what the scaffold set up (e.g. if Express is the server \
framework, use Express; if React Router is installed, use React Router).

## After implementing

1. Run `npm run build` (or equivalent) to verify there are no compilation errors
2. Run the test suite and fix any failures
3. Verify your implementation is complete — search your own code for TODO, FIXME, \
placeholder, "implement", or stub patterns and replace them with real code
"""


def retry_prompt(task: Task, errors: str) -> str:
    criteria_str = "\n".join(f"  - {c}" for c in task.acceptance_criteria)

    return f"""\
The previous implementation attempt for "{task.name}" had issues:

```
{errors}
```

## What to do

1. Read the error output carefully and identify every distinct failure
2. Read the relevant source files to understand the current state
3. Fix ALL issues — not just the first one
4. Check your implementation is complete:
   - No TODO/FIXME/placeholder/stub code
   - All functions fully implemented with real logic
   - All imports resolve correctly
   - All components render real content (not placeholder text)
5. Run the build and test suite again to verify

## Acceptance criteria (for reference):
{criteria_str}
"""


def scaffold_prompt() -> str:
    return """\
Based on the architecture at docs/ARCHITECTURE.md and the build plan \
at docs/BUILD_PLAN.md, create the full project scaffold.

## Read first

1. Read `docs/PRD.md` for product requirements
2. Read `docs/ARCHITECTURE.md` for tech stack decisions, data models, API contracts
3. Read `docs/BUILD_PLAN.md` for the task breakdown

## Create the scaffold

1. **Directory structure** matching the architecture exactly
2. **Package manager config** with ALL dependencies needed for the full build \
(not just the scaffold — include everything from the architecture doc: \
UI libraries, database drivers, auth packages, testing frameworks, etc.)
3. **Language config** (tsconfig.json, .eslintrc, etc.)
4. **Database schema or ORM models** — fully define all tables/models from the \
architecture doc with all columns, types, relations, and indexes
5. **API route stubs** — create the route files with correct paths and HTTP methods \
from the architecture doc. Each route handler should have the correct function \
signature and return type, but implementation will come in later tasks.
6. **Shared types/interfaces** — define all TypeScript interfaces, API request/response \
types, and shared constants referenced in the architecture
7. **Test infrastructure** (vitest/jest/pytest config, test helpers, fixtures)
8. **Environment config** (.env.example with all required vars documented)
9. **README** with setup instructions

## Rules

- Install ALL dependencies and verify the project builds with zero errors
- The scaffold should compile/build clean — no type errors, no missing imports
- Define real data models with all fields — not just `id` and `name`
- Route stubs should have correct paths, methods, and parameter types
- Do NOT leave TODO comments — if something belongs in a later task, \
leave a minimal valid implementation (e.g. an empty array response, \
a component that renders its name) rather than a TODO
"""
