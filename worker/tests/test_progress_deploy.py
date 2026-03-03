"""Tests for deploy tracking fields in ProgressTracker."""
from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.progress import ProgressTracker


class TestProgressDeployTracking:
    def test_set_deploy_info(self, tmp_path: Path):
        docs = tmp_path / "docs"
        docs.mkdir()
        tracker = ProgressTracker(str(tmp_path), "test-job")
        tracker.set_deploy_info("sod-abc123", "https://sod-abc123.fly.dev")

        data = json.loads((docs / "PROGRESS.json").read_text())
        assert data["deploy_app_name"] == "sod-abc123"
        assert data["deploy_url"] == "https://sod-abc123.fly.dev"

    def test_get_deploy_info(self, tmp_path: Path):
        docs = tmp_path / "docs"
        docs.mkdir()
        tracker = ProgressTracker(str(tmp_path), "test-job")
        tracker.set_deploy_info("sod-abc123", "https://sod-abc123.fly.dev")

        # Reload from disk
        tracker2 = ProgressTracker(str(tmp_path), "test-job")
        assert tracker2.progress.deploy_app_name == "sod-abc123"
        assert tracker2.progress.deploy_url == "https://sod-abc123.fly.dev"

    def test_default_deploy_fields_empty(self, tmp_path: Path):
        docs = tmp_path / "docs"
        docs.mkdir()
        tracker = ProgressTracker(str(tmp_path), "test-job")
        assert tracker.progress.deploy_app_name == ""
        assert tracker.progress.deploy_url == ""
