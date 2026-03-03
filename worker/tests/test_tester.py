"""Tests for the E2E tester report parser."""
from pathlib import Path

from src.pipeline.tester import parse_test_report


class TestParseTestReport:
    """Tests for parse_test_report()."""

    def test_no_report_file(self, tmp_path: Path):
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 0
        assert result["all_passed"] is False
        assert result["failed_flows"] == []

    def test_all_passed(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 10\n"
            "passed: 10\n"
            "failed: 0\n"
            "blocked: 0\n\n"
            "## Results\n\n"
            "### PASS: tenant-login (2.1s)\nAll steps passed.\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 10
        assert result["passed"] == 10
        assert result["failed"] == 0
        assert result["blocked"] == 0
        assert result["all_passed"] is True

    def test_with_failures(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 10\n"
            "passed: 7\n"
            "failed: 2\n"
            "blocked: 1\n\n"
            "## Results\n\n"
            "### PASS: tenant-login (2.1s)\n\n"
            "### FAIL: create-building (5.2s)\n"
            "Failed at step 4\n\n"
            "### FAIL: password-reset (3.1s)\n"
            "Failed at step 6\n\n"
            "### BLOCKED: view-buildings\n"
            "reason: depends on create-building\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 10
        assert result["passed"] == 7
        assert result["failed"] == 2
        assert result["blocked"] == 1
        assert result["all_passed"] is False
        assert result["failed_flows"] == ["create-building", "password-reset"]
        assert result["blocked_flows"] == ["view-buildings"]

    def test_zero_total_not_passed(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 0\n"
            "passed: 0\n"
            "failed: 0\n"
            "blocked: 0\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["all_passed"] is False  # 0 total means nothing ran
