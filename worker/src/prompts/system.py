from __future__ import annotations

from pathlib import Path

# Default paths — overridden by config in production
CLAUDE_CONFIG_PATH = Path("/app/claude-config")
VP_SKILL_PATH = Path("/app/visual-playwright/SKILL.md")


def set_config_path(path: str) -> None:
    """Override the claude config path (for testing)."""
    global CLAUDE_CONFIG_PATH
    CLAUDE_CONFIG_PATH = Path(path)


def set_vp_skill_path(path: str) -> None:
    """Override the VP skill path (for testing)."""
    global VP_SKILL_PATH
    VP_SKILL_PATH = Path(path)


def load_agent(name: str) -> str:
    """Load an agent definition from everything-claude-code.

    Args:
        name: Agent filename, e.g. 'architect.md', 'planner.md'
    """
    path = CLAUDE_CONFIG_PATH / "agents" / name
    if not path.exists():
        print(f"[prompts] Warning: agent '{name}' not found at {path}")
        return ""
    return path.read_text()


def load_skills(skill_names: list[str]) -> str:
    """Load and concatenate skill definitions from multiple skill directories."""
    parts: list[str] = []
    for name in skill_names:
        skill_dir = CLAUDE_CONFIG_PATH / "skills" / name
        if not skill_dir.exists():
            print(f"[prompts] Warning: skill '{name}' not found at {skill_dir}")
            continue
        for md_file in sorted(skill_dir.glob("*.md")):
            parts.append(md_file.read_text())
    return "\n\n---\n\n".join(parts)


def load_skill(name: str) -> str:
    """Load a single skill definition by name.

    Checks Visual Playwright first, then everything-claude-code skills.
    """
    if name == "visual-playwright":
        if VP_SKILL_PATH.exists():
            return VP_SKILL_PATH.read_text()
        print(f"[prompts] Warning: VP skill not found at {VP_SKILL_PATH}")
        return ""

    skill_dir = CLAUDE_CONFIG_PATH / "skills" / name
    if not skill_dir.exists():
        print(f"[prompts] Warning: skill '{name}' not found at {skill_dir}")
        return ""

    parts: list[str] = []
    for md_file in sorted(skill_dir.glob("*.md")):
        parts.append(md_file.read_text())
    return "\n\n".join(parts)


def load_rules(rule_names: list[str]) -> str:
    """Load rule files as system prompt additions.

    Checks common rules first, then language-specific.
    """
    parts: list[str] = []
    for name in rule_names:
        # Try common rules first
        path = CLAUDE_CONFIG_PATH / "rules" / "common" / f"{name}.md"
        if not path.exists():
            # Try as a direct path
            path = CLAUDE_CONFIG_PATH / "rules" / f"{name}.md"
        if path.exists():
            parts.append(path.read_text())
        else:
            print(f"[prompts] Warning: rule '{name}' not found")
    return "\n\n".join(parts)
