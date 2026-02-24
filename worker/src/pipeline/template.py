"""Copy a template into the target repo as a starting point for the build."""
from __future__ import annotations

import shutil
from pathlib import Path


def apply_template(
    template_name: str,
    repo_path: str,
    templates_base: str = "/app/templates",
) -> bool:
    """Copy template files into repo_path, skipping files that already exist.

    Returns True if the template was applied, False if not found.
    """
    template_dir = Path(templates_base) / template_name
    if not template_dir.is_dir():
        print(f"[template] Template '{template_name}' not found at {template_dir}")
        return False

    repo = Path(repo_path)

    for src_file in template_dir.rglob("*"):
        if src_file.is_dir():
            continue

        rel_path = src_file.relative_to(template_dir)
        dest_file = repo / rel_path

        # Skip files that already exist in the repo (don't overwrite user's work)
        if dest_file.exists():
            print(f"[template] Skipping {rel_path} (already exists)")
            continue

        dest_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)

    print(f"[template] Applied template '{template_name}' to {repo_path}")
    return True
