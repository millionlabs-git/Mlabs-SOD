"""Post-deploy test data seeder — populates the DB with test accounts and data."""
from __future__ import annotations

from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.testing import seed_prompt
from src.pipeline.agent import run_agent


async def seed_test_data(
    repo_path: str,
    db_url: str,
    config: Config,
    reporter: StatusReporter,
) -> bool:
    """Seed test data into the deployed database.

    Reads docs/SEED_DATA.md from the repo, then runs an agent that
    writes and executes a seed script against the live DB.

    Returns True if seeding succeeded, False otherwise.
    """
    await reporter.report("seeding_started")
    print("[seeder] Starting test data seeding...")

    seed_data_path = Path(repo_path) / "docs" / "SEED_DATA.md"
    if not seed_data_path.exists():
        print("[seeder] No SEED_DATA.md found — skipping seeding")
        await reporter.report("seeding_skipped", {"reason": "no SEED_DATA.md"})
        return True  # Not a failure, just nothing to seed

    seed_data_content = seed_data_path.read_text()

    try:
        await run_agent(
            prompt=seed_prompt(seed_data_content, db_url),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
            cwd=repo_path,
            model=config.model,
            max_turns=20,
            reporter=reporter,
            agent_label="seeder",
        )
        await reporter.report("seeding_complete")
        print("[seeder] Test data seeded successfully")
        return True

    except Exception as e:
        print(f"[seeder] Seeding failed: {e}")
        await reporter.report("seeding_failed", {"error": str(e)[:500]})
        return False
