"""LLM-as-Judge — cheap Haiku call to check acceptance criteria after each task."""
from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from claude_agent_sdk import (
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    query,
)


@dataclass
class JudgeResult:
    """Outcome of an LLM acceptance-criteria check."""

    passed: bool
    evidence: str
    missing: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _extract_json(text: str) -> dict | None:
    """Extract a JSON object from agent output text."""
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _default_pass() -> JudgeResult:
    """Return a default pass — never block the pipeline on judge failure."""
    return JudgeResult(passed=True, evidence="judge unavailable", missing=[])


def _parse_result(text: str) -> JudgeResult:
    """Parse agent output into a JudgeResult."""
    data = _extract_json(text)
    if data is None:
        return _default_pass()

    try:
        passed = data.get("passed", True)
        if not isinstance(passed, bool):
            passed = True

        evidence = str(data.get("evidence", ""))

        missing = data.get("missing", [])
        if not isinstance(missing, list):
            missing = [str(missing)]
        missing = [str(m) for m in missing]

        return JudgeResult(passed=passed, evidence=evidence, missing=missing)
    except (TypeError, KeyError, ValueError):
        return _default_pass()


async def judge_task(
    task_name: str,
    acceptance_criteria: str,
    repo_path: str,
) -> JudgeResult:
    """Check whether a task's acceptance criteria are met using Haiku."""
    try:
        prompt = (
            f"You are an acceptance-criteria judge for task: {task_name}\n\n"
            f"## Acceptance Criteria\n{acceptance_criteria}\n\n"
            "## Instructions\n"
            "1. Read the source code in this repository\n"
            "2. For each acceptance criterion, check if the code actually implements it\n"
            "3. Look for real implementations, not stubs or TODOs\n"
            "4. Return ONLY a JSON object:\n"
            '```json\n{"passed": true/false, "evidence": "brief summary", '
            '"missing": ["criterion that is not met"]}\n```\n'
            "If ALL criteria are met, set passed=true and missing=[].\n"
            "If ANY criterion is NOT met, set passed=false and list the missing ones."
        )

        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Bash", "Grep", "Glob"],
            permission_mode="bypassPermissions",
            cwd=repo_path,
            model="claude-haiku-4-5",
            max_turns=3,
            sandbox={"enabled": False},
        )

        text_parts: list[str] = []
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                cost = f"${message.total_cost_usd:.4f}" if message.total_cost_usd else "?"
                print(f"[llm-judge] Done — turns: {message.num_turns}, cost: {cost}")

        return _parse_result("\n".join(text_parts))

    except Exception as exc:
        print(f"[llm-judge] Failed for task '{task_name}': {exc}")
        return _default_pass()


def main() -> None:
    """CLI: python -m src.orchestrator.llm_judge <task_name> <acceptance_criteria> [--root DIR]."""
    import argparse

    parser = argparse.ArgumentParser(description="LLM acceptance criteria judge")
    parser.add_argument("task_name", help="Name of the task to judge")
    parser.add_argument("acceptance_criteria", help="Acceptance criteria text")
    parser.add_argument("--root", default=".", help="Repository root (default: cwd)")
    args = parser.parse_args()

    result = asyncio.run(judge_task(args.task_name, args.acceptance_criteria, args.root))
    print(result.to_json())
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
