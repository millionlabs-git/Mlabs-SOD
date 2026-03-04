"""Tests for the E2E tester report parser."""
from pathlib import Path

from src.pipeline.tester import (
    parse_test_report,
    _parse_batch_report,
    _match_flow_ids,
    _slugify,
)


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
            "total_flows: 3\n"
            "passed: 3\n"
            "failed: 0\n"
            "blocked: 0\n\n"
            "## Results\n\n"
            "### PASS: tenant-login (2.1s)\nAll steps passed.\n\n"
            "### PASS: create-building (3.0s)\nAll steps passed.\n\n"
            "### PASS: view-buildings (1.5s)\nAll steps passed.\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 3
        assert result["passed"] == 3
        assert result["failed"] == 0
        assert result["blocked"] == 0
        assert result["all_passed"] is True

    def test_with_failures(self, tmp_path: Path):
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 4\n"
            "passed: 1\n"
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
        # Header counting is primary source of truth
        assert result["total"] == 4
        assert result["passed"] == 1
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

    def test_summary_fallback_when_no_headers(self, tmp_path: Path):
        """When there are no ### headers, fall back to summary counts."""
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "# E2E Test Report\n\n"
            "## Summary\n"
            "total_flows: 5\n"
            "passed: 3\n"
            "failed: 1\n"
            "blocked: 1\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["total"] == 5
        assert result["passed"] == 3
        assert result["failed"] == 1
        assert result["blocked"] == 1

    def test_failed_variant_spelling(self, tmp_path: Path):
        """Parser should accept both FAIL and FAILED."""
        report = tmp_path / "docs" / "TEST_REPORT.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "## Results\n\n"
            "### FAILED: create-building\n"
            "Some error\n\n"
            "### PASS: tenant-login\n"
        )
        result = parse_test_report(str(tmp_path))
        assert result["failed"] == 1
        assert result["passed"] == 1


class TestParseBatchReport:
    """Tests for _parse_batch_report() with known flow ID matching."""

    def test_no_file(self, tmp_path: Path):
        result = _parse_batch_report(str(tmp_path), 0)
        assert result["total"] == 0

    def test_header_counting(self, tmp_path: Path):
        """Headers are counted even when summary says 0."""
        report = tmp_path / "docs" / "TEST_REPORT_BATCH_0.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "## Summary\n"
            "total_flows: 0\n"
            "passed: 0\n"
            "failed: 0\n"
            "blocked: 0\n\n"
            "## Results\n\n"
            "### PASS: signup-email-password\nAll steps passed.\n\n"
            "### FAIL: login-invalid-credentials\nFailed at step 2\n\n"
            "### BLOCKED: view-dashboard\nreason: login failed\n"
        )
        result = _parse_batch_report(str(tmp_path), 0)
        assert result["total"] == 3
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["blocked"] == 1

    def test_known_flow_id_matching(self, tmp_path: Path):
        """Agent writes human-readable names; parser matches to known slugs."""
        report = tmp_path / "docs" / "TEST_REPORT_BATCH_0.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "## Results\n\n"
            "### PASS: 1.1 Signup (email/password)\nAll passed.\n\n"
            "### FAIL: 1.2 Login — Invalid Credentials\nFailed.\n\n"
            "### BLOCKED: 1.3 View Dashboard\nBlocked.\n"
        )
        known = ["signup-email-password", "login-invalid-credentials", "view-dashboard"]
        result = _parse_batch_report(str(tmp_path), 0, known_flow_ids=known)
        assert result["passed"] == 1
        assert result["failed"] == 1
        assert result["blocked"] == 1
        assert "signup-email-password" in result["passed_flows"]
        assert "login-invalid-credentials" in result["failed_flows"]
        assert "view-dashboard" in result["blocked_flows"]

    def test_numbered_flow_ids(self, tmp_path: Path):
        """Agent writes just numbers like '### PASS: 4' — should match via stripping."""
        report = tmp_path / "docs" / "TEST_REPORT_BATCH_0.md"
        report.parent.mkdir(parents=True)
        report.write_text(
            "## Results\n\n"
            "### PASS: 4.15 — Manage Duplicates: Merge\nDone.\n"
        )
        known = ["manage-duplicates-merge"]
        result = _parse_batch_report(str(tmp_path), 0, known_flow_ids=known)
        assert result["passed"] == 1
        assert "manage-duplicates-merge" in result["passed_flows"]


class TestMatchFlowIds:
    """Tests for _match_flow_ids()."""

    def test_exact_match(self):
        assert _match_flow_ids(["signup"], ["signup", "login"]) == ["signup"]

    def test_slugify_match(self):
        assert _match_flow_ids(["Signup (email/password)"], ["signup-email-password"]) == ["signup-email-password"]

    def test_substring_match(self):
        result = _match_flow_ids(["manage-duplicates"], ["manage-duplicates-merge"])
        assert result == ["manage-duplicates-merge"]

    def test_numbered_prefix_stripping(self):
        result = _match_flow_ids(
            ["4.15 — Manage Duplicates: Merge"],
            ["manage-duplicates-merge"],
        )
        assert result == ["manage-duplicates-merge"]

    def test_no_known_ids_slugifies(self):
        result = _match_flow_ids(["Some Flow Name"], None)
        assert result == ["some-flow-name"]

    def test_empty_entries(self):
        assert _match_flow_ids([], ["signup"]) == []
