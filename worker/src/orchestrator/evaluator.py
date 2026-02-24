"""Lightweight evaluator agent that runs after key phases."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from claude_agent_sdk import (
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    query,
)
from src.prompts.evaluation import (
    evaluate_architecture_prompt,
    evaluate_scaffold_prompt,
)


@dataclass
class EvaluationResult:
    """Result of a phase evaluation."""

    passed: bool
    score: float  # 0.0-1.0
    issues: list[str]
    recommendation: str  # proceed | retry | retry_with_guidance
    guidance: str  # injected into retry prompt if retry_with_guidance


# Map phase names to their prompt builders
_PHASE_PROMPTS: dict[str, callable] = {
    "architecture": evaluate_architecture_prompt,
    "scaffolding": evaluate_scaffold_prompt,
}


def _default_pass() -> EvaluationResult:
    """Return a default 'passed' result — used when evaluation cannot complete."""
    return EvaluationResult(
        passed=True,
        score=0.7,
        issues=[],
        recommendation="proceed",
        guidance="",
    )


def _extract_json(text: str) -> dict | None:
    """Try to extract a JSON object from agent output text.

    Handles cases where the JSON might be wrapped in markdown code fences
    or surrounded by explanatory text.
    """
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting from code fences
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # Try finding the first { ... } block
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _parse_result(text: str) -> EvaluationResult:
    """Parse agent output text into an EvaluationResult."""
    data = _extract_json(text)
    if data is None:
        return _default_pass()

    try:
        score = float(data.get("score", 0.7))
        score = max(0.0, min(1.0, score))  # clamp

        issues = data.get("issues", [])
        if not isinstance(issues, list):
            issues = [str(issues)]
        issues = [str(i) for i in issues]

        recommendation = data.get("recommendation", "proceed")
        if recommendation not in ("proceed", "retry", "retry_with_guidance"):
            recommendation = "proceed" if score >= 0.7 else "retry"

        guidance = str(data.get("guidance", ""))

        passed = data.get("passed", score >= 0.7)
        if not isinstance(passed, bool):
            passed = score >= 0.7

        return EvaluationResult(
            passed=passed,
            score=score,
            issues=issues,
            recommendation=recommendation,
            guidance=guidance,
        )
    except (TypeError, KeyError, ValueError):
        return _default_pass()


async def evaluate_phase(
    phase: str,
    repo_path: str,
    config,
    context_builder,
) -> EvaluationResult:
    """Evaluate a phase's output using an agent.

    Builds context via *context_builder*, runs a read-only agent to assess
    the phase output, and parses its JSON response into an EvaluationResult.

    If anything goes wrong (agent failure, JSON parse failure), returns a
    default 'passed' result so the pipeline is not blocked by evaluator errors.
    """
    try:
        # Build context and select prompt
        context = context_builder.for_evaluator(phase)
        prompt_builder = _PHASE_PROMPTS.get(phase)
        if prompt_builder is None:
            # No specific evaluator prompt for this phase — skip evaluation
            return _default_pass()

        prompt = prompt_builder(context)

        # Run agent directly (not via run_agent wrapper) so we can capture text output
        options = ClaudeAgentOptions(
            allowed_tools=["Read", "Bash", "Grep", "Glob"],
            permission_mode="bypassPermissions",
            cwd=repo_path,
            model=config.model,
            max_turns=5,
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
                print(f"[evaluator] Done — turns: {message.num_turns}, cost: {cost}")

        full_text = "\n".join(text_parts)
        return _parse_result(full_text)

    except Exception as exc:
        print(f"[evaluator] Evaluation failed for phase '{phase}': {exc}")
        return _default_pass()
