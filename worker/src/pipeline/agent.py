"""Wrapper around the Claude Agent SDK for running agent queries and agent teams."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)

if TYPE_CHECKING:
    from src.status import StatusReporter


@dataclass
class AgentResult:
    """Structured result returned by run_agent."""

    cost_usd: float
    turns: int
    duration_ms: int
    is_error: bool


def _summarize_tool(name: str, inp: dict) -> str:
    """One-line human-readable summary of a tool use."""
    if name == "Bash":
        cmd = inp.get("command", "")
        if len(cmd) > 120:
            cmd = cmd[:120] + "..."
        return f"$ {cmd}"
    if name == "Read":
        path = inp.get("file_path", "?")
        return f"Read {path.split('/')[-1]}" if "/" in path else f"Read {path}"
    if name in ("Write", "Edit"):
        path = inp.get("file_path", "?")
        return f"{name} {path.split('/')[-1]}" if "/" in path else f"{name} {path}"
    if name == "Grep":
        pattern = inp.get("pattern", "?")
        return f"Grep '{pattern[:60]}'"
    if name == "Glob":
        return f"Glob '{inp.get('pattern', '?')}'"
    if name == "Task":
        desc = inp.get("description", inp.get("prompt", ""))[:80]
        return f"Task: {desc}"
    return name


async def run_agent(
    prompt: str,
    *,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    mcp_servers: dict | None = None,
    cwd: str = ".",
    model: str = "claude-sonnet-4-6",
    max_turns: int | None = None,
    context: str = "",
    agents: dict[str, AgentDefinition] | None = None,
    reporter: "StatusReporter | None" = None,
    agent_label: str = "agent",
) -> AgentResult:
    """Run a Claude agent query and return the final result.

    Streams messages to stdout for logging. Returns an AgentResult
    with cost and usage info.

    Args:
        prompt: The task prompt for the agent.
        system_prompt: Optional system instructions.
        allowed_tools: Tool whitelist. Defaults to standard dev tools.
            When *agents* are provided, "Task" is automatically added.
        mcp_servers: Optional MCP server configurations.
        cwd: Working directory for Bash commands.
        model: Model to use.
        max_turns: Maximum number of agentic turns.
        context: Project context injected at the top of the prompt.
        agents: Subagent definitions. The orchestrator agent invokes
            these via the Task tool based on each agent's description.
        reporter: Optional StatusReporter to emit granular log events.
        agent_label: Descriptive label for this agent run (used in events).
    """
    if context:
        prompt = f"## Project Context\n\n{context}\n\n---\n\n{prompt}"

    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]

    # Task tool is required for the orchestrator to invoke subagents
    if agents and "Task" not in allowed_tools:
        allowed_tools = [*allowed_tools, "Task"]

    options = ClaudeAgentOptions(
        system_prompt=system_prompt if system_prompt else None,
        allowed_tools=allowed_tools,
        mcp_servers=mcp_servers if mcp_servers else None,
        permission_mode="bypassPermissions",
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        sandbox={"enabled": False},
        agents=agents if agents else None,
    )

    # Emit agent_started event
    if reporter:
        await reporter.report("agent_started", {
            "agent_label": agent_label,
            "model": model,
            "max_turns": max_turns,
        })

    start_ms = time.monotonic_ns() // 1_000_000
    result: ResultMessage | None = None
    turn_count = 0
    # Each entry: {"tool": "Bash", "detail": "npm run build"}
    tool_details_since_last: list[dict[str, str]] = []
    last_text = ""
    progress_interval = 3

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                turn_count += 1
                for block in message.content:
                    if isinstance(block, TextBlock):
                        text = block.text
                        if len(text) > 500:
                            text = text[:500] + "..."
                        print(f"[{agent_label}] {text}")
                        last_text = text
                    elif isinstance(block, ToolUseBlock):
                        detail = _summarize_tool(block.name, block.input)
                        print(f"[{agent_label}] {detail}")
                        tool_details_since_last.append({
                            "tool": block.name,
                            "detail": detail,
                        })

                # Emit progress every N turns
                if reporter and turn_count % progress_interval == 0:
                    await reporter.report("agent_progress", {
                        "agent_label": agent_label,
                        "turn": turn_count,
                        "actions": tool_details_since_last[:],
                        "summary": last_text[:400] if last_text else "",
                    })
                    tool_details_since_last.clear()

            elif isinstance(message, ResultMessage):
                result = message
                cost = f"${result.total_cost_usd:.4f}" if result.total_cost_usd else "unknown"
                print(
                    f"[{agent_label}] Done — turns: {result.num_turns}, "
                    f"cost: {cost}, "
                    f"duration: {result.duration_ms}ms"
                )
    except Exception as exc:
        elapsed = (time.monotonic_ns() // 1_000_000) - start_ms
        if reporter:
            await reporter.report("agent_error", {
                "agent_label": agent_label,
                "error": str(exc)[:500],
                "turn": turn_count,
                "duration_ms": elapsed,
            })
        raise

    if result is None:
        raise RuntimeError("Agent query completed without a ResultMessage")

    if result.is_error:
        if reporter:
            await reporter.report("agent_error", {
                "agent_label": agent_label,
                "error": str(result)[:500],
                "turn": turn_count,
                "duration_ms": result.duration_ms or 0,
            })
        raise RuntimeError(f"Agent query failed: {result}")

    # Emit agent_completed event
    if reporter:
        await reporter.report("agent_completed", {
            "agent_label": agent_label,
            "turns": result.num_turns or 0,
            "cost_usd": result.total_cost_usd or 0.0,
            "duration_ms": result.duration_ms or 0,
        })

    return AgentResult(
        cost_usd=result.total_cost_usd or 0.0,
        turns=result.num_turns or 0,
        duration_ms=result.duration_ms or 0,
        is_error=result.is_error,
    )
