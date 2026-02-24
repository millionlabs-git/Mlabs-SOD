from __future__ import annotations


def architecture_prompt(prd_content: str) -> str:
    return f"""\
Read the following PRD and produce an architecture design document.

Decide on:
- Tech stack (languages, frameworks, databases, hosting)
- Directory structure
- Major components and their responsibilities
- Data models and schemas
- API contracts (endpoints, request/response shapes)
- Key interfaces between components
- Authentication and authorization approach
- Error handling strategy

Write the architecture doc to docs/ARCHITECTURE.md in the repo.

PRD:
{prd_content}
"""


def task_decomposition_prompt() -> str:
    return """\
Based on the architecture doc at docs/ARCHITECTURE.md and the PRD at docs/PRD.md, \
break the implementation into an ordered list of tasks.

## Task design rules

Each task should:
- Be a **complete, functional vertical slice** — not just "create file X". A task \
should produce working, connected code (e.g. "Implement user registration" includes \
the API endpoint, database query, input validation, and error handling).
- Have clear, **verifiable** acceptance criteria — things you can check by running \
the code, not vague statements like "code is clean"
- Specify which files will be created or modified
- Note dependencies on other tasks
- Be tagged with has_ui: true/false
- Include acceptance criteria

## Task ordering

1. **Database layer first** — models, schema, seed data, database utility functions
2. **API/backend routes** — each route group as a task (auth routes, CRUD routes, etc.)
3. **Frontend pages and components** — each major page or feature as a task
4. **Integration and polish** — connecting frontend to backend, navigation, error \
handling, loading states

## Important

- Target **8-15 tasks** total. Too few = tasks are too large for one agent pass. \
Too many = overhead and lost context between tasks.
- Each task's description should be detailed enough (2-4 sentences) that an agent \
with no prior context can implement it by reading the description + architecture doc.
- Acceptance criteria should be **specific and testable**: "POST /api/auth/register \
creates a user and returns a JWT" not "registration works".

## Format

For each task use this exact format:

## Task N: <name>
- **Description:** What to build (2-4 sentences with specifics)
- **Files:** List of files to create/modify
- **Dependencies:** Task numbers this depends on (or None)
- **Has UI:** true/false
- **Route:** /path (if has_ui is true, the URL path to verify)
- **Acceptance Criteria:**
  - Criterion 1
  - Criterion 2

Write the plan to docs/BUILD_PLAN.md
"""
