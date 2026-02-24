"""Wrapper around the Claude Agent SDK for running agent queries and agent teams."""
from __future__ import annotations

from dataclasses import dataclass

from claude_agent_sdk import (
    AgentDefinition,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)


@dataclass
class AgentResult:
    """Structured result returned by run_agent."""

    cost_usd: float
    turns: int
    duration_ms: int
    is_error: bool


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

    result: ResultMessage | None = None

    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    # Truncate long outputs for logging
                    text = block.text
                    if len(text) > 500:
                        text = text[:500] + "..."
                    print(f"[agent] {text}")
                elif isinstance(block, ToolUseBlock):
                    print(f"[agent] Tool: {block.name}")
        elif isinstance(message, ResultMessage):
            result = message
            cost = f"${result.total_cost_usd:.4f}" if result.total_cost_usd else "unknown"
            print(
                f"[agent] Done — turns: {result.num_turns}, "
                f"cost: {cost}, "
                f"duration: {result.duration_ms}ms"
            )

    if result is None:
        raise RuntimeError("Agent query completed without a ResultMessage")

    if result.is_error:
        raise RuntimeError(f"Agent query failed: {result}")

    return AgentResult(
        cost_usd=result.total_cost_usd or 0.0,
        turns=result.num_turns or 0,
        duration_ms=result.duration_ms or 0,
        is_error=result.is_error,
    )
