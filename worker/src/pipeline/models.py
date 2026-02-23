from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Task:
    name: str
    description: str
    target_files: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    has_ui: bool = False
    route: str | None = None
    acceptance_criteria: list[str] = field(default_factory=list)


@dataclass
class BuildPlan:
    tasks: list[Task]

    @property
    def total_tasks(self) -> int:
        return len(self.tasks)

    @property
    def ui_task_count(self) -> int:
        return sum(1 for t in self.tasks if t.has_ui)


def parse_build_plan(plan_path: str) -> BuildPlan:
    """Parse BUILD_PLAN.md into a structured BuildPlan.

    Expects the format:
    ## Task N: <name>
    - **Description:** ...
    - **Files:** ...
    - **Dependencies:** ...
    - **Has UI:** true/false
    - **Route:** /path
    - **Acceptance Criteria:**
      - Criterion 1
      - Criterion 2
    """
    content = Path(plan_path).read_text()
    tasks: list[Task] = []

    # Split by task headers
    task_sections = re.split(r"^## Task \d+:\s*", content, flags=re.MULTILINE)

    for section in task_sections:
        section = section.strip()
        if not section:
            continue

        lines = section.split("\n")
        name = lines[0].strip()

        description = ""
        target_files: list[str] = []
        dependencies: list[str] = []
        has_ui = False
        route: str | None = None
        criteria: list[str] = []
        in_criteria = False

        for line in lines[1:]:
            stripped = line.strip()

            if stripped.startswith("- **Description:**"):
                description = stripped.replace("- **Description:**", "").strip()
                in_criteria = False
            elif stripped.startswith("- **Files:**"):
                files_str = stripped.replace("- **Files:**", "").strip()
                target_files = [f.strip() for f in files_str.split(",") if f.strip()]
                in_criteria = False
            elif stripped.startswith("- **Dependencies:**"):
                deps_str = stripped.replace("- **Dependencies:**", "").strip()
                if deps_str.lower() not in ("none", "n/a", "-", ""):
                    dependencies = [d.strip() for d in deps_str.split(",") if d.strip()]
                in_criteria = False
            elif stripped.startswith("- **Has UI:**"):
                ui_str = stripped.replace("- **Has UI:**", "").strip().lower()
                has_ui = ui_str in ("true", "yes", "1")
                in_criteria = False
            elif stripped.startswith("- **Route:**"):
                route_str = stripped.replace("- **Route:**", "").strip()
                route = route_str if route_str and route_str != "-" else None
                in_criteria = False
            elif stripped.startswith("- **Acceptance Criteria:**"):
                in_criteria = True
            elif in_criteria and stripped.startswith("- "):
                criteria.append(stripped[2:].strip())

        if name:
            tasks.append(Task(
                name=name,
                description=description,
                target_files=target_files,
                dependencies=dependencies,
                has_ui=has_ui,
                route=route,
                acceptance_criteria=criteria,
            ))

    return BuildPlan(tasks=tasks)
