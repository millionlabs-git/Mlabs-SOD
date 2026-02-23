from __future__ import annotations

import os
from pydantic import BaseModel, Field


class Config(BaseModel):
    """Worker configuration — all values come from environment variables."""

    # Job identity (set by orchestrator per execution)
    job_id: str
    repo_url: str
    branch: str = "main"
    prd_path: str = "docs/PRD.md"

    # Orchestrator callback
    orchestrator_url: str
    webhook_secret: str

    # API keys (set as Cloud Run Job secrets)
    anthropic_api_key: str
    github_token: str

    # Model
    model: str = "claude-sonnet-4-5-20250929"

    # Build settings
    max_task_retries: int = 3
    task_timeout: int = 300

    # Paths (internal to the container)
    claude_config_path: str = "/app/claude-config"
    vp_script_path: str = "/app/visual-playwright/scripts/vp.mjs"
    workspace_path: str = "/workspace"

    @classmethod
    def from_env(cls) -> Config:
        """Load config from environment variables."""
        return cls(
            job_id=os.environ["JOB_ID"],
            repo_url=os.environ["REPO_URL"],
            branch=os.environ.get("BRANCH", "main"),
            prd_path=os.environ.get("PRD_PATH", "docs/PRD.md"),
            orchestrator_url=os.environ["ORCHESTRATOR_URL"],
            webhook_secret=os.environ["WEBHOOK_SECRET"],
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            github_token=os.environ["GITHUB_TOKEN"],
            model=os.environ.get("MODEL", "claude-sonnet-4-5-20250929"),
            max_task_retries=int(os.environ.get("MAX_TASK_RETRIES", "3")),
            task_timeout=int(os.environ.get("TASK_TIMEOUT", "300")),
            claude_config_path=os.environ.get("CLAUDE_CONFIG_PATH", "/app/claude-config"),
            vp_script_path=os.environ.get("VP_SCRIPT_PATH", "/app/visual-playwright/scripts/vp.mjs"),
            workspace_path=os.environ.get("WORKSPACE_PATH", "/workspace"),
        )
