"""Phase 6: Deploy to Neon DB + Netlify."""
from __future__ import annotations

import json
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.system import load_skill
from src.prompts.deploy import (
    neon_provision_prompt,
    schema_migration_prompt,
    production_build_prompt,
    netlify_deploy_prompt,
    deployment_verify_prompt,
)
from src.pipeline.agent import run_agent
from src.repo import git_commit, git_push


def _needs_db(repo_path: str) -> bool:
    """Detect whether the project has database schema files."""
    repo = Path(repo_path)
    indicators = [
        repo / "prisma" / "schema.prisma",
        repo / "drizzle.config.ts",
        repo / "drizzle.config.js",
        repo / "schema.sql",
        repo / "migrations",
        repo / "db" / "migrate",
        repo / "drizzle",
    ]
    return any(p.exists() for p in indicators)


def _neon_mcp(config: Config) -> dict:
    return {
        "neon": {
            "command": "npx",
            "args": ["-y", "@neondatabase/mcp-server-neon", "start"],
            "env": {"NEON_API_KEY": config.neon_api_key},
        }
    }


def _netlify_mcp(config: Config) -> dict:
    return {
        "netlify": {
            "command": "npx",
            "args": ["-y", "@netlify/mcp"],
            "env": {"NETLIFY_AUTH_TOKEN": config.netlify_auth_token},
        }
    }


async def deploy(
    repo_path: str,
    config: Config,
    reporter: StatusReporter,
    branch_name: str | None = None,
) -> dict:
    """Provision DB (if needed), build, deploy to Netlify, and verify."""
    await reporter.report("deploy_started")
    print("[deployer] Starting deployment phase")

    has_db = _needs_db(repo_path) and config.neon_api_key
    db_url: str | None = None
    neon_project_id: str | None = None

    # --- Step 1: Provision Neon DB (if needed) ---
    if has_db:
        print("[deployer] Database schema detected — provisioning Neon DB...")
        await reporter.report("neon_provisioning")

        await run_agent(
            prompt=neon_provision_prompt(config.job_id),
            allowed_tools=["Bash", "Write", "Read"],
            mcp_servers=_neon_mcp(config),
            cwd=repo_path,
            model=config.model,
            max_turns=10,
        )

        # Read credentials saved by agent
        creds_file = Path("/tmp/neon-credentials.json")
        if creds_file.exists():
            creds = json.loads(creds_file.read_text())
            db_url = creds.get("database_url")
            neon_project_id = creds.get("project_id")
            print(f"[deployer] Neon DB provisioned: project={neon_project_id}")
        else:
            print("[deployer] Warning: Neon credentials file not found")

        # --- Step 2: Run schema migration ---
        if db_url:
            print("[deployer] Running schema migration...")
            await reporter.report("schema_migrating")

            await run_agent(
                prompt=schema_migration_prompt(db_url),
                allowed_tools=["Bash", "Read", "Write", "Edit", "Grep", "Glob"],
                mcp_servers=_neon_mcp(config),
                cwd=repo_path,
                model=config.model,
                max_turns=15,
            )
    else:
        if _needs_db(repo_path):
            print("[deployer] Database schema detected but NEON_API_KEY not set — skipping DB provisioning")
        else:
            print("[deployer] No database schema detected — skipping DB provisioning")

    # --- Step 3: Production build ---
    print("[deployer] Running production build...")
    await reporter.report("production_building")

    await run_agent(
        prompt=production_build_prompt(db_url),
        allowed_tools=["Bash", "Read", "Write", "Edit", "Grep", "Glob"],
        cwd=repo_path,
        model=config.model,
        max_turns=15,
    )

    # --- Step 4: Deploy to Netlify ---
    print("[deployer] Deploying to Netlify...")
    await reporter.report("netlify_deploying")

    env_vars_hint = ""
    if db_url:
        env_vars_hint = f'\n   - DATABASE_URL="{db_url}"'

    await run_agent(
        prompt=netlify_deploy_prompt(config.job_id, env_vars_hint),
        allowed_tools=["Bash", "Read", "Write", "Grep", "Glob"],
        mcp_servers=_netlify_mcp(config),
        cwd=repo_path,
        model=config.model,
        max_turns=15,
    )

    # Read deployment info saved by agent
    deploy_file = Path("/tmp/netlify-deployment.json")
    live_url: str | None = None
    netlify_site_id: str | None = None

    if deploy_file.exists():
        deploy_info = json.loads(deploy_file.read_text())
        live_url = deploy_info.get("site_url")
        netlify_site_id = deploy_info.get("site_id")
        print(f"[deployer] Deployed to: {live_url}")
    else:
        print("[deployer] Warning: Netlify deployment info file not found")

    # --- Step 5: Verify live site ---
    if live_url:
        print("[deployer] Verifying live deployment...")
        await reporter.report("deploy_verifying")

        screenshots_dir = f"{repo_path}/docs/screenshots/deploy"
        Path(screenshots_dir).mkdir(parents=True, exist_ok=True)

        vp_system = load_skill("visual-playwright")

        try:
            await run_agent(
                prompt=deployment_verify_prompt(
                    live_url, config.vp_script_path, screenshots_dir, bool(has_db)
                ),
                system_prompt=vp_system,
                allowed_tools=["Bash", "Read", "Write", "Grep", "Glob"],
                cwd=repo_path,
                model=config.model,
                max_turns=10,
            )
        except Exception as e:
            print(f"[deployer] Deployment verification error (non-fatal): {e}")

    # --- Step 6: Commit deployment artifacts and report ---
    git_commit(repo_path, "docs: add deployment info and verification")
    if branch_name:
        git_push(repo_path, branch_name)

    result = {
        "live_url": live_url,
        "netlify_site_id": netlify_site_id,
        "neon_project_id": neon_project_id,
    }

    await reporter.report("deployed", result)
    print(f"[deployer] Deployment complete: {result}")

    return result
