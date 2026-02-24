from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TechProfile:
    languages: list[str] = field(default_factory=list)
    frontend_framework: str | None = None
    backend_framework: str | None = None
    database: str | None = None
    orm: str | None = None
    agents: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    needs_db_reviewer: bool = False


def _read_text(path: Path) -> str | None:
    """Read a file as text, returning None if it does not exist."""
    try:
        return path.read_text()
    except (FileNotFoundError, OSError):
        return None


def _check_package_json(repo: Path, profile: TechProfile) -> None:
    """Detect tech from package.json."""
    content = _read_text(repo / "package.json")
    if content is None:
        return

    try:
        pkg = json.loads(content)
    except json.JSONDecodeError:
        return

    profile.languages.append("javascript")

    deps = {}
    deps.update(pkg.get("dependencies", {}))
    dev_deps = pkg.get("devDependencies", {})
    deps.update(dev_deps)

    if "typescript" in dev_deps or "typescript" in pkg.get("dependencies", {}):
        profile.languages.append("typescript")

    # Frontend frameworks
    frontend_map = {
        "react": "react",
        "next": "next.js",
        "vue": "vue",
        "svelte": "svelte",
        "@angular/core": "angular",
    }
    for dep_key, framework_name in frontend_map.items():
        if dep_key in deps and profile.frontend_framework is None:
            profile.frontend_framework = framework_name

    # Backend frameworks
    backend_map = {
        "express": "express",
        "fastify": "fastify",
        "koa": "koa",
        "@nestjs/core": "nestjs",
    }
    for dep_key, framework_name in backend_map.items():
        if dep_key in deps and profile.backend_framework is None:
            profile.backend_framework = framework_name

    # Databases
    db_map = {
        "pg": "postgres",
        "mysql2": "mysql",
        "mongodb": "mongodb",
        "better-sqlite3": "sqlite",
    }
    for dep_key, db_name in db_map.items():
        if dep_key in deps and profile.database is None:
            profile.database = db_name

    # ORMs
    orm_map = {
        "prisma": "prisma",
        "@prisma/client": "prisma",
        "drizzle-orm": "drizzle",
        "typeorm": "typeorm",
        "sequelize": "sequelize",
    }
    for dep_key, orm_name in orm_map.items():
        if dep_key in deps and profile.orm is None:
            profile.orm = orm_name


def _check_python(repo: Path, profile: TechProfile) -> None:
    """Detect Python tech stack."""
    pyproject = _read_text(repo / "pyproject.toml")
    requirements = _read_text(repo / "requirements.txt")

    if pyproject is None and requirements is None:
        return

    profile.languages.append("python")

    # Combine both files for keyword scanning
    combined = (pyproject or "") + "\n" + (requirements or "")
    combined_lower = combined.lower()

    backend_map = {
        "django": "django",
        "flask": "flask",
        "fastapi": "fastapi",
    }
    for keyword, framework_name in backend_map.items():
        if keyword in combined_lower and profile.backend_framework is None:
            profile.backend_framework = framework_name

    orm_map = {
        "sqlalchemy": "sqlalchemy",
        "django": "django",
    }
    for keyword, orm_name in orm_map.items():
        if keyword in combined_lower and profile.orm is None:
            profile.orm = orm_name


def _check_go(repo: Path, profile: TechProfile) -> None:
    """Detect Go."""
    if (repo / "go.mod").exists():
        profile.languages.append("go")


def _check_schema_files(repo: Path, profile: TechProfile) -> None:
    """Detect from schema/migration files."""
    if (repo / "prisma" / "schema.prisma").exists():
        if profile.orm is None:
            profile.orm = "prisma"
        if profile.database is None:
            profile.database = "postgres"

    # Check for drizzle config
    for suffix in [".ts", ".js", ".mjs", ".mts"]:
        if (repo / f"drizzle.config{suffix}").exists():
            if profile.orm is None:
                profile.orm = "drizzle"
            break

    # Check for schema.sql or migrations directories
    has_schema_sql = (repo / "schema.sql").exists()
    has_migrations = any(repo.glob("**/migrations"))
    if (has_schema_sql or has_migrations) and profile.database is None:
        profile.database = "postgres"


def _scan_architecture_md(repo: Path, profile: TechProfile) -> None:
    """Scan docs/ARCHITECTURE.md for keywords to fill gaps."""
    content = _read_text(repo / "docs" / "ARCHITECTURE.md")
    if content is None:
        return

    content_lower = content.lower()

    # Fill in gaps only
    if profile.database is None:
        db_keywords = {
            "postgres": "postgres",
            "postgresql": "postgres",
            "mysql": "mysql",
            "mongodb": "mongodb",
            "sqlite": "sqlite",
        }
        for keyword, db_name in db_keywords.items():
            if keyword in content_lower:
                profile.database = db_name
                break

    if profile.frontend_framework is None:
        fe_keywords = {
            "react": "react",
            "next.js": "next.js",
            "nextjs": "next.js",
            "vue": "vue",
            "svelte": "svelte",
            "angular": "angular",
        }
        for keyword, name in fe_keywords.items():
            if keyword in content_lower:
                profile.frontend_framework = name
                break

    if profile.backend_framework is None:
        be_keywords = {
            "express": "express",
            "fastify": "fastify",
            "koa": "koa",
            "nestjs": "nestjs",
            "django": "django",
            "flask": "flask",
            "fastapi": "fastapi",
        }
        for keyword, name in be_keywords.items():
            if keyword in content_lower:
                profile.backend_framework = name
                break


def _map_to_components(profile: TechProfile) -> None:
    """Map detected tech to agents, skills, and rules."""
    # Always include
    profile.skills.append("coding-standards")
    profile.rules.extend(["coding-style", "testing", "security"])

    # Language-specific rules and agents
    if "typescript" in profile.languages:
        profile.rules.extend([
            "typescript/coding-style",
            "typescript/patterns",
            "typescript/testing",
            "typescript/security",
        ])

    if "python" in profile.languages:
        profile.rules.extend([
            "python/coding-style",
            "python/patterns",
            "python/testing",
            "python/security",
        ])
        profile.agents.append("python-reviewer.md")

    if "go" in profile.languages:
        profile.rules.extend([
            "golang/coding-style",
            "golang/patterns",
            "golang/testing",
            "golang/security",
        ])
        profile.agents.append("go-reviewer.md")

    # Framework-based skills
    if profile.frontend_framework:
        profile.skills.append("frontend-patterns")

    if profile.backend_framework:
        profile.skills.extend(["backend-patterns", "api-design"])

    # Database-related
    if profile.database:
        profile.skills.append("database-migrations")
        profile.needs_db_reviewer = True

    if profile.database == "postgres":
        profile.skills.append("postgres-patterns")
        profile.agents.append("database-reviewer.md")

    # Always add these
    profile.rules.extend(["performance", "patterns"])
    profile.agents.append("build-error-resolver.md")


def detect_tech_stack(repo_path: str) -> TechProfile:
    """Scan repo files and ARCHITECTURE.md to detect tech stack and select components."""
    repo = Path(repo_path)
    profile = TechProfile()

    _check_package_json(repo, profile)
    _check_python(repo, profile)
    _check_go(repo, profile)
    _check_schema_files(repo, profile)
    _scan_architecture_md(repo, profile)
    _map_to_components(profile)

    return profile
