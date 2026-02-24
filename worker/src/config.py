from __future__ import annotations

import os
from pydantic import BaseModel


class Config(BaseModel):
    """Worker configuration — all values come from environment variables."""

    # Job identity (set by orchestrator per execution)
    job_id: str
    repo_url: str
    branch: str = "main"
    prd_path: str = "docs/PRD.md"
    mode: str = "full-build"  # full-build | deploy-only | auto

    # Orchestrator callback
    orchestrator_url: str
    webhook_secret: str

    # API keys (set as Cloud Run Job secrets)
    anthropic_api_key: str

    # GitHub App credentials
    github_app_id: str
    github_app_installation_id: str
    github_app_private_key: str  # PEM-encoded RSA private key

    # Deploy credentials (optional — deploy phase skipped if not set)
    neon_api_key: str = ""
    fly_api_token: str = ""

    # Model
    model: str = "claude-sonnet-4-6"

    # Build settings
    max_task_retries: int = 3
    task_timeout: int = 300

    # Template settings
    template: str = "saas-starter"  # template name under templates_path
    templates_path: str = "/app/templates"

    # Paths (internal to the container)
    claude_config_path: str = "/app/claude-config"
    vp_script_path: str = "/app/visual-playwright/scripts/vp.mjs"
    workspace_path: str = "/workspace"

    @classmethod
    def from_env(cls) -> Config:
        """Load config from environment variables."""
        # Private key may be base64-encoded (for env var transport)
        private_key = os.environ["GITHUB_APP_PRIVATE_KEY"]
        if not private_key.startswith("-----"):
            import base64
            private_key = base64.b64decode(private_key).decode()

        return cls(
            job_id=os.environ["JOB_ID"],
            repo_url=os.environ["REPO_URL"],
            branch=os.environ.get("BRANCH", "main"),
            prd_path=os.environ.get("PRD_PATH", "docs/PRD.md"),
            mode=os.environ.get("MODE", "full-build"),
            orchestrator_url=os.environ["ORCHESTRATOR_URL"],
            webhook_secret=os.environ["WEBHOOK_SECRET"],
            anthropic_api_key=os.environ["ANTHROPIC_API_KEY"],
            github_app_id=os.environ["GITHUB_APP_ID"],
            github_app_installation_id=os.environ["GITHUB_APP_INSTALLATION_ID"],
            github_app_private_key=private_key,
            neon_api_key=os.environ.get("NEON_API_KEY", ""),
            fly_api_token=os.environ.get("FLY_API_TOKEN", ""),
            model=os.environ.get("MODEL", "claude-sonnet-4-6"),
            max_task_retries=int(os.environ.get("MAX_TASK_RETRIES", "3")),
            task_timeout=int(os.environ.get("TASK_TIMEOUT", "300")),
            template=os.environ.get("TEMPLATE", "saas-starter"),
            templates_path=os.environ.get("TEMPLATES_PATH", "/app/templates"),
            claude_config_path=os.environ.get("CLAUDE_CONFIG_PATH", "/app/claude-config"),
            vp_script_path=os.environ.get("VP_SCRIPT_PATH", "/app/visual-playwright/scripts/vp.mjs"),
            workspace_path=os.environ.get("WORKSPACE_PATH", "/workspace"),
        )
