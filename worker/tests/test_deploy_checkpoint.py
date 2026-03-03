"""Tests for deploy_checkpoint function."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.pipeline.deployer import deploy_checkpoint


class TestDeployCheckpoint:
    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_success(self, mock_run):
        """Health check returns 200 -> pass."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="deployed", stderr=""),  # flyctl deploy
            MagicMock(returncode=0, stdout="200", stderr=""),  # curl health check
        ]
        result = deploy_checkpoint("/fake/repo", "test-app", "smoke-test")
        assert result.passed is True
        assert result.issues == []

    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_health_fail(self, mock_run):
        """Health check returns non-200 -> fail."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="deployed", stderr=""),  # flyctl deploy
            MagicMock(returncode=0, stdout="502", stderr=""),  # curl health check
        ]
        result = deploy_checkpoint("/fake/repo", "test-app", "smoke-test")
        assert result.passed is False
        assert any("health" in i.lower() for i in result.issues)

    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_deploy_fail(self, mock_run):
        """flyctl deploy fails -> fail immediately."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="deploy error"
        )
        result = deploy_checkpoint("/fake/repo", "test-app", "smoke-test")
        assert result.passed is False
        assert any("flyctl deploy failed" in i for i in result.issues)

    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_timeout(self, mock_run):
        """Deploy timeout -> fail."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="flyctl", timeout=180)
        result = deploy_checkpoint("/fake/repo", "test-app", "smoke-test")
        assert result.passed is False
        assert any("timed out" in i.lower() for i in result.issues)

    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_custom_health_path(self, mock_run):
        """Custom health path is used in curl."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="deployed", stderr=""),
            MagicMock(returncode=0, stdout="200", stderr=""),
        ]
        result = deploy_checkpoint(
            "/fake/repo", "test-app", "midpoint", health_path="/api/health"
        )
        assert result.passed is True
        # Verify the curl call used the custom path
        curl_call = mock_run.call_args_list[1]
        curl_args = curl_call[0][0]  # positional args to subprocess.run
        url_arg = [a for a in curl_args if "fly.dev" in a][0]
        assert "/api/health" in url_arg
