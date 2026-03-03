"""Tests for LLM-as-Judge (llm_judge.py)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestrator.llm_judge import (
    JudgeResult,
    _extract_json,
    _parse_result,
    judge_task,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_assistant_message(text: str):
    """Create a mock AssistantMessage with a single TextBlock."""
    block = MagicMock()
    block.text = text
    # isinstance checks need real types, so we patch in the test
    return ("assistant", block)


# ---------------------------------------------------------------------------
# JudgeResult
# ---------------------------------------------------------------------------

class TestJudgeResult:
    def test_to_json(self):
        r = JudgeResult(passed=True, evidence="all good", missing=[])
        data = json.loads(r.to_json())
        assert data["passed"] is True
        assert data["evidence"] == "all good"
        assert data["missing"] == []

    def test_to_json_with_missing(self):
        r = JudgeResult(passed=False, evidence="gaps found", missing=["search filters"])
        data = json.loads(r.to_json())
        assert data["passed"] is False
        assert len(data["missing"]) == 1


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

class TestExtractJson:
    def test_plain_json(self):
        data = _extract_json('{"passed": true, "evidence": "ok", "missing": []}')
        assert data["passed"] is True

    def test_json_in_code_fence(self):
        text = 'Some text\n```json\n{"passed": false, "missing": ["auth"]}\n```\nMore text'
        data = _extract_json(text)
        assert data["passed"] is False
        assert data["missing"] == ["auth"]

    def test_json_in_plain_fence(self):
        text = '```\n{"passed": true}\n```'
        data = _extract_json(text)
        assert data["passed"] is True

    def test_no_json(self):
        assert _extract_json("no json here") is None


# ---------------------------------------------------------------------------
# _parse_result
# ---------------------------------------------------------------------------

class TestParseResult:
    def test_valid_passed(self):
        r = _parse_result('{"passed": true, "evidence": "all criteria met", "missing": []}')
        assert r.passed is True
        assert r.evidence == "all criteria met"
        assert r.missing == []

    def test_valid_failed(self):
        r = _parse_result('{"passed": false, "evidence": "gaps", "missing": ["search"]}')
        assert r.passed is False
        assert "search" in r.missing

    def test_invalid_json_returns_default(self):
        r = _parse_result("not json at all")
        assert r.passed is True
        assert r.evidence == "judge unavailable"


# ---------------------------------------------------------------------------
# judge_task (async, mocked)
# ---------------------------------------------------------------------------

class TestJudgeTask:
    @patch("src.orchestrator.llm_judge.query")
    def test_judge_passed(self, mock_query):
        """Mock query returns passed JSON -> JudgeResult.passed is True."""
        from src.orchestrator.llm_judge import AssistantMessage, TextBlock, ResultMessage

        text_block = MagicMock(spec=TextBlock)
        text_block.text = '{"passed": true, "evidence": "all criteria met", "missing": []}'

        assistant_msg = MagicMock(spec=AssistantMessage)
        assistant_msg.content = [text_block]

        result_msg = MagicMock(spec=ResultMessage)
        result_msg.total_cost_usd = 0.001
        result_msg.num_turns = 1

        async def fake_query(**kwargs):
            yield assistant_msg
            yield result_msg

        mock_query.side_effect = fake_query

        result = asyncio.run(judge_task("task-1", "Users can search", "/fake"))
        assert result.passed is True
        assert result.missing == []

    @patch("src.orchestrator.llm_judge.query")
    def test_judge_failed(self, mock_query):
        """Mock query returns failed JSON -> JudgeResult.passed is False."""
        from src.orchestrator.llm_judge import AssistantMessage, TextBlock, ResultMessage

        text_block = MagicMock(spec=TextBlock)
        text_block.text = '{"passed": false, "evidence": "missing search", "missing": ["search filters"]}'

        assistant_msg = MagicMock(spec=AssistantMessage)
        assistant_msg.content = [text_block]

        result_msg = MagicMock(spec=ResultMessage)
        result_msg.total_cost_usd = 0.001
        result_msg.num_turns = 2

        async def fake_query(**kwargs):
            yield assistant_msg
            yield result_msg

        mock_query.side_effect = fake_query

        result = asyncio.run(judge_task("task-1", "Search filters work", "/fake"))
        assert result.passed is False
        assert "search filters" in result.missing

    @patch("src.orchestrator.llm_judge.query")
    def test_judge_error_returns_pass(self, mock_query):
        """On exception, return default pass (never block pipeline)."""
        mock_query.side_effect = RuntimeError("SDK unavailable")

        result = asyncio.run(judge_task("task-1", "criteria", "/fake"))
        assert result.passed is True
        assert result.evidence == "judge unavailable"
