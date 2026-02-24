from __future__ import annotations

from pathlib import Path

from src.orchestrator.tech_detector import TechProfile


class ComponentLoader:
    """Load agents/skills/rules from everything-claude-code based on TechProfile."""

    def __init__(self, config_path: str, vp_skill_path: str, tech_profile: TechProfile):
        self.config_path = Path(config_path)
        self.vp_skill_path = Path(vp_skill_path)
        self.tech_profile = tech_profile

    def _load_agent(self, name: str) -> str:
        """Read from config_path/agents/{name}, return empty string if not found."""
        path = self.config_path / "agents" / name
        try:
            return path.read_text()
        except (FileNotFoundError, OSError):
            return ""

    def _load_skill(self, name: str) -> str:
        """Load a skill by name.

        If 'visual-playwright', read from vp_skill_path.
        Otherwise read all .md files from config_path/skills/{name}/ and concatenate.
        Return empty string if directory not found.
        """
        if name == "visual-playwright":
            try:
                return self.vp_skill_path.read_text()
            except (FileNotFoundError, OSError):
                return ""

        skill_dir = self.config_path / "skills" / name
        if not skill_dir.is_dir():
            return ""

        parts = []
        for md_file in sorted(skill_dir.glob("*.md")):
            try:
                parts.append(md_file.read_text())
            except OSError:
                continue
        return "\n\n".join(parts)

    def _load_rule(self, name: str) -> str:
        """Load a rule by name.

        Try config_path/rules/common/{name}.md first,
        then config_path/rules/{name}.md (for paths like 'typescript/coding-style').
        Return empty string if not found.
        """
        # Try common path first
        common_path = self.config_path / "rules" / "common" / f"{name}.md"
        if common_path.exists():
            try:
                return common_path.read_text()
            except OSError:
                pass

        # Try direct path (e.g. rules/typescript/coding-style.md)
        direct_path = self.config_path / "rules" / f"{name}.md"
        if direct_path.exists():
            try:
                return direct_path.read_text()
            except OSError:
                pass

        return ""

    def _load_tech_rules(self) -> str:
        """Load all rules from self.tech_profile.rules and concatenate."""
        parts = [self._load_rule(r) for r in self.tech_profile.rules]
        return self._combine(parts)

    def _load_tech_skills(self) -> str:
        """Load all skills from self.tech_profile.skills and concatenate."""
        parts = [self._load_skill(s) for s in self.tech_profile.skills]
        return self._combine(parts)

    def _combine(self, parts: list[str]) -> str:
        """Join non-empty parts with a separator."""
        non_empty = [p for p in parts if p.strip()]
        return "\n\n---\n\n".join(non_empty)

    # -- Public methods: phase-specific system prompts --

    def for_architect(self) -> str:
        """Build system prompt for the architect phase."""
        return self._combine([
            self._load_agent("architect.md"),
            self._load_skill("api-design"),
            self._load_tech_rules(),
        ])

    def for_planner(self) -> str:
        """Build system prompt for the planner phase."""
        return self._combine([
            self._load_agent("planner.md"),
        ])

    def for_scaffolder(self) -> str:
        """Build system prompt for the scaffolder phase."""
        return self._combine([
            self._load_tech_skills(),
            self._load_tech_rules(),
        ])

    def for_builder(self) -> str:
        """Build system prompt for the builder phase."""
        return self._combine([
            self._load_skill("tdd-workflow"),
            self._load_skill("verification-loop"),
            self._load_tech_rules(),
        ])

    def for_build_error_resolver(self) -> str:
        """Build system prompt for the build error resolver phase."""
        return self._combine([
            self._load_agent("build-error-resolver.md"),
            self._load_rule("coding-style"),
        ])

    def for_reviewer(self) -> str:
        """Build system prompt for the reviewer phase."""
        return self._combine([
            self._load_agent("code-reviewer.md"),
            self._load_skill("security-review"),
            self._load_tech_rules(),
        ])

    def for_security_reviewer(self) -> str:
        """Build system prompt for the security reviewer phase."""
        return self._combine([
            self._load_agent("security-reviewer.md"),
            self._load_skill("security-scan"),
        ])

    def for_db_reviewer(self) -> str:
        """Build system prompt for the database reviewer phase."""
        return self._combine([
            self._load_agent("database-reviewer.md"),
            self._load_skill("postgres-patterns"),
            self._load_skill("database-migrations"),
        ])

    def for_e2e_runner(self) -> str:
        """Build system prompt for the e2e runner phase."""
        return self._combine([
            self._load_agent("e2e-runner.md"),
            self._load_skill("e2e-testing"),
            self._load_skill("visual-playwright"),
        ])
