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
Based on the architecture doc at docs/ARCHITECTURE.md, break the implementation \
into an ordered list of tasks.

Each task should:
- Be small enough to implement in a single focused pass
- Have clear inputs and outputs
- Be independently testable
- Specify which files will be created or modified
- Note dependencies on other tasks
- Be tagged with has_ui: true/false (does this task produce visible UI?)
- Include acceptance criteria

Format the plan as a structured markdown document. For each task use this format:

## Task N: <name>
- **Description:** What to build
- **Files:** List of files to create/modify
- **Dependencies:** Task numbers this depends on
- **Has UI:** true/false
- **Route:** /path (if has_ui is true, the URL path to verify)
- **Acceptance Criteria:**
  - Criterion 1
  - Criterion 2

Write the plan to docs/BUILD_PLAN.md
"""
