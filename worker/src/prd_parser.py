from __future__ import annotations

from pathlib import Path


def parse_prd(repo_path: str, prd_path: str) -> str:
    """Read the PRD file from the cloned repo and return its content.

    For v1, we return raw markdown. The planner agent will interpret it.
    Future: extract structured sections (features, acceptance criteria, constraints).
    """
    full_path = Path(repo_path) / prd_path
    if not full_path.exists():
        raise FileNotFoundError(
            f"PRD not found at {full_path}. "
            f"Expected at '{prd_path}' relative to repo root."
        )
    content = full_path.read_text()
    if not content.strip():
        raise ValueError(f"PRD file at {full_path} is empty.")
    return content
