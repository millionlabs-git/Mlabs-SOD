"""Build context strings from phase outputs for injection into agent prompts."""
from __future__ import annotations

import json
from pathlib import Path


class ContextBuilder:
    """Build context strings from phase outputs for injection into agent prompts."""

    MAX_FILE_SIZE = 8000  # chars, truncate files longer than this
    MAX_CONTEXT_SIZE = 12000  # chars, total context budget

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)

    # ── helpers ──────────────────────────────────────────────────────────

    def _read_file(self, relative_path: str) -> str:
        """Read a file from the repo, truncate if > MAX_FILE_SIZE.

        Returns empty string if the file does not exist.
        """
        path = self.repo_path / relative_path
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            return ""
        return self._truncate(content, self.MAX_FILE_SIZE)

    def _read_file_if_exists(self, relative_path: str, label: str) -> str:
        """Return ``## {label}\\n{content}`` if the file exists, else empty string."""
        content = self._read_file(relative_path)
        if not content:
            return ""
        return f"## {label}\n{content}\n"

    def _truncate(self, text: str, max_chars: int) -> str:
        """Truncate *text* to *max_chars* with a marker when trimmed."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... (truncated)"

    # ── public context builders ──────────────────────────────────────────

    def for_scaffolder(self) -> str:
        """Return context for the scaffold phase.

        Includes the PRD, architecture doc, and build plan.
        """
        parts: list[str] = []
        parts.append(self._read_file_if_exists("docs/PRD.md", "Product Requirements (PRD)"))
        parts.append(self._read_file_if_exists("docs/ARCHITECTURE.md", "Architecture"))
        parts.append(self._read_file_if_exists("docs/BUILD_PLAN.md", "Build Plan"))
        return "\n".join(p for p in parts if p)

    def for_builder(
        self,
        task_name: str,
        task_files: list[str],
        completed_tasks: list[str],
    ) -> str:
        """Return context for a build task.

        Includes trimmed PRD and architecture, existing file contents for the
        files this task will touch, and a list of already-completed tasks.
        """
        budget = self.MAX_CONTEXT_SIZE
        sections: list[str] = []

        # PRD (first 3000 chars)
        prd = self._read_file("docs/PRD.md")
        if prd:
            prd = self._truncate(prd, 3000)
            section = f"## Product Requirements (PRD)\n{prd}\n"
            sections.append(section)
            budget -= len(section)

        # Architecture (first 3000 chars)
        arch = self._read_file("docs/ARCHITECTURE.md")
        if arch:
            arch = self._truncate(arch, 3000)
            section = f"## Architecture\n{arch}\n"
            sections.append(section)
            budget -= len(section)

        # Existing file contents for files this task will create/modify
        if task_files:
            file_parts: list[str] = []
            per_file_budget = max(500, budget // max(len(task_files), 1))
            for fpath in task_files:
                content = self._read_file(fpath)
                if content:
                    content = self._truncate(content, per_file_budget)
                    file_parts.append(f"### {fpath}\n```\n{content}\n```")
            if file_parts:
                section = "## Existing Files\n" + "\n".join(file_parts) + "\n"
                sections.append(section)
                budget -= len(section)

        # Completed tasks
        if completed_tasks:
            task_list = "\n".join(f"- {t}" for t in completed_tasks)
            section = f"## Completed Tasks\n{task_list}\n"
            sections.append(section)

        return "\n".join(sections)

    def for_reviewer(self) -> str:
        """Return context for the review phase.

        Includes PRD (for requirements mapping), architecture, and a summary
        of completed phases from PROGRESS.json.
        """
        parts: list[str] = []
        parts.append(self._read_file_if_exists("docs/PRD.md", "Product Requirements (PRD)"))
        parts.append(self._read_file_if_exists("docs/ARCHITECTURE.md", "Architecture"))

        # Read PROGRESS.json for phase completion info
        progress_content = self._read_file("docs/PROGRESS.json")
        if progress_content:
            try:
                progress = json.loads(progress_content)
                phases = progress.get("phases", {})
                if phases:
                    lines = []
                    for name, info in phases.items():
                        status = info.get("status", "unknown")
                        score = info.get("evaluation_score")
                        score_str = f" (score: {score})" if score is not None else ""
                        lines.append(f"- {name}: {status}{score_str}")
                    parts.append("## Pipeline Progress\n" + "\n".join(lines) + "\n")
            except (json.JSONDecodeError, AttributeError):
                pass

        # List source files in the repo (excluding docs, node_modules, etc.)
        source_files = self._list_source_files()
        if source_files:
            file_list = "\n".join(f"- {f}" for f in source_files[:100])
            parts.append(f"## Source Files ({len(source_files)} total)\n{file_list}\n")

        return "\n".join(p for p in parts if p)

    def for_evaluator(self, phase: str) -> str:
        """Return context for the evaluator to assess a phase's output.

        Content varies by phase:
        - architecture: ARCHITECTURE.md + PRD
        - scaffolding: ARCHITECTURE.md + list of source files created
        """
        parts: list[str] = []

        if phase == "architecture":
            parts.append(self._read_file_if_exists("docs/PRD.md", "Product Requirements (PRD)"))
            parts.append(self._read_file_if_exists("docs/ARCHITECTURE.md", "Architecture Document"))
            parts.append(
                "## Evaluation Criteria\n"
                "1. Does the architecture address every feature in the PRD?\n"
                "2. Are data models complete (all tables, columns, relations)?\n"
                "3. Are API endpoints defined for every feature?\n"
                "4. Is the tech stack reasonable for the requirements?\n"
                "5. Are there clear component boundaries and data flow?\n"
            )

        elif phase == "scaffolding":
            parts.append(self._read_file_if_exists("docs/ARCHITECTURE.md", "Architecture Document"))
            source_files = self._list_source_files()
            if source_files:
                file_list = "\n".join(f"- {f}" for f in source_files[:150])
                parts.append(f"## Source Files Created ({len(source_files)} total)\n{file_list}\n")
            parts.append(
                "## Evaluation Criteria\n"
                "1. Does the project build successfully (`npm run build` or equivalent)?\n"
                "2. Does the directory structure match the architecture?\n"
                "3. Are all dependencies from the architecture installed?\n"
                "4. Is there excessive TODO/placeholder code?\n"
                "5. Are configuration files (tsconfig, eslint, etc.) properly set up?\n"
            )

        else:
            # Generic fallback for other phases
            parts.append(self._read_file_if_exists("docs/PRD.md", "Product Requirements (PRD)"))
            parts.append(self._read_file_if_exists("docs/ARCHITECTURE.md", "Architecture Document"))
            progress_content = self._read_file("docs/PROGRESS.json")
            if progress_content:
                parts.append(f"## Pipeline Progress\n{progress_content}\n")
            parts.append(
                f"## Evaluation Criteria\n"
                f"Evaluate the output of the '{phase}' phase for completeness and quality.\n"
            )

        return "\n".join(p for p in parts if p)

    # ── internal utilities ───────────────────────────────────────────────

    def _list_source_files(self) -> list[str]:
        """List source files in the repo, excluding common non-source directories."""
        exclude_dirs = {
            "node_modules", ".git", ".next", "dist", "build", "__pycache__",
            ".venv", "venv", ".claude", ".cache", "coverage", ".turbo",
        }
        exclude_extensions = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg"}
        files: list[str] = []

        try:
            for path in sorted(self.repo_path.rglob("*")):
                if not path.is_file():
                    continue
                # Skip excluded directories
                rel = path.relative_to(self.repo_path)
                parts = rel.parts
                if any(part in exclude_dirs for part in parts):
                    continue
                # Skip excluded extensions
                if path.suffix.lower() in exclude_extensions:
                    continue
                files.append(str(rel))
        except (PermissionError, OSError):
            pass

        return files
