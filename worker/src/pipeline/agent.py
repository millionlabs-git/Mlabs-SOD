"""Thin wrapper around the Claude Agent SDK for running agent queries."""
from __future__ import annotations

from claude_agent_sdk import (
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    query,
)


async def run_agent(
    prompt: str,
    *,
    system_prompt: str = "",
    allowed_tools: list[str] | None = None,
    cwd: str = ".",
    model: str = "claude-sonnet-4-5-20250929",
    max_turns: int | None = None,
) -> ResultMessage:
    """Run a Claude agent query and return the final result.

    Streams messages to stdout for logging. Returns the ResultMessage
    with cost and usage info.
    """
    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Grep", "Glob"]

    options = ClaudeAgentOptions(
        system_prompt=system_prompt if system_prompt else None,
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        cwd=cwd,
        model=model,
        max_turns=max_turns,
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

    return result
