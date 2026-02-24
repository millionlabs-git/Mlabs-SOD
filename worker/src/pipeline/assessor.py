"""Maturity assessment — agent-based evaluation of codebase vs PRD."""
from __future__ import annotations

import json
from pathlib import Path

from src.config import Config
from src.status import StatusReporter
from src.prompts.assessment import maturity_assessment_prompt
from src.pipeline.agent import run_agent


async def assess_maturity(
    repo_path: str,
    prd_content: str,
    config: Config,
    reporter: StatusReporter,
) -> dict[str, bool]:
    """Run an agent to assess how much of the PRD is already implemented.

    Returns a skip dict compatible with _detect_completed_phases() format.
    """
    await reporter.report("assessment_started")
    print("[assessor] Running maturity assessment against PRD...")

    await run_agent(
        prompt=maturity_assessment_prompt(prd_content),
        allowed_tools=["Read", "Bash", "Grep", "Glob"],
        cwd=repo_path,
        model=config.model,
        max_turns=20,
    )

    # Parse the assessment JSON written by the agent
    assessment_file = Path("/tmp/assessment.json")
    if not assessment_file.exists():
        print("[assessor] Warning: assessment file not found, defaulting to full build")
        await reporter.report("assessment_complete", {"result": "fallback_full_build"})
        return {}

    try:
        assessment = json.loads(assessment_file.read_text())
    except (json.JSONDecodeError, Exception) as e:
        print(f"[assessor] Warning: failed to parse assessment ({e}), defaulting to full build")
        await reporter.report("assessment_complete", {"result": "fallback_full_build"})
        return {}

    skip = {
        "planning": bool(assessment.get("planning_complete", False)),
        "scaffolding": bool(assessment.get("scaffolding_complete", False)),
        "building": bool(assessment.get("building_complete", False)),
        "review": bool(assessment.get("review_complete", False)),
    }

    skipped = [phase for phase, done in skip.items() if done]
    remaining = [phase for phase, done in skip.items() if not done]

    summary = assessment.get("summary", "no summary")
    coverage = assessment.get("feature_coverage", "unknown")
    needs_fixes = assessment.get("needs_fixes", [])

    print(f"[assessor] Assessment complete — coverage: {coverage}")
    print(f"[assessor] Skip: {', '.join(skipped) or 'none'}")
    print(f"[assessor] Remaining: {', '.join(remaining) or 'none'}")
    if needs_fixes:
        print(f"[assessor] Issues: {', '.join(needs_fixes[:5])}")

    await reporter.report("assessment_complete", {
        "skip": skip,
        "feature_coverage": coverage,
        "summary": summary,
        "needs_fixes": needs_fixes,
    })

    return skip
