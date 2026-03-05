"""Tests for email integration module."""
import json
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

import pytest

from src.pipeline.models import EmailTemplate
from src.config import Config


class TestEmailTemplate:
    def test_create_email_template(self):
        t = EmailTemplate(
            alias="welcome",
            name="Welcome Email",
            subject="Welcome to {{app_name}}",
            html_body="<h1>Welcome {{name}}</h1>",
            text_body="Welcome {{name}}",
        )
        assert t.alias == "welcome"
        assert t.subject == "Welcome to {{app_name}}"

    def test_email_template_defaults(self):
        t = EmailTemplate(
            alias="reset",
            name="Password Reset",
            subject="Reset your password",
            html_body="<p>Click here</p>",
            text_body="Click here",
        )
        assert t.name == "Password Reset"


class TestEmailConfig:
    def test_config_has_email_fields(self):
        c = Config(
            job_id="test-123",
            repo_url="https://github.com/test/repo",
            orchestrator_url="http://localhost:8080",
            webhook_secret="secret",
            anthropic_api_key="sk-test",
            github_app_id="123",
            github_app_installation_id="456",
            github_app_private_key="-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
        )
        assert c.postmark_account_api_key == ""
        assert c.private_email_host == "mail.privateemail.com"
        assert c.private_email_user == ""
        assert c.private_email_password == ""

    def test_config_from_env_reads_email_fields(self, monkeypatch):
        required = {
            "JOB_ID": "j1",
            "REPO_URL": "https://github.com/t/r",
            "ORCHESTRATOR_URL": "http://localhost",
            "WEBHOOK_SECRET": "s",
            "ANTHROPIC_API_KEY": "sk",
            "GITHUB_APP_ID": "1",
            "GITHUB_APP_INSTALLATION_ID": "2",
            "GITHUB_APP_PRIVATE_KEY": "-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----",
            "POSTMARK_ACCOUNT_API_KEY": "pm-test-key",
            "PRIVATE_EMAIL_USER": "hello@millionlabs.digital",
            "PRIVATE_EMAIL_PASSWORD": "testpass",
        }
        for k, v in required.items():
            monkeypatch.setenv(k, v)
        c = Config.from_env()
        assert c.postmark_account_api_key == "pm-test-key"
        assert c.private_email_user == "hello@millionlabs.digital"
        assert c.private_email_password == "testpass"


class TestPostmarkManager:
    def test_import(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")
        assert pm.account_api_key == "test-key"

    @pytest.mark.asyncio
    async def test_ensure_server_creates_new(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")
        mock_response_list = MagicMock()
        mock_response_list.json.return_value = {"Servers": []}
        mock_response_list.raise_for_status = MagicMock()
        mock_response_create = MagicMock()
        mock_response_create.json.return_value = {"ID": 12345, "ApiTokens": ["srv-token-abc"]}
        mock_response_create.raise_for_status = MagicMock()
        with patch("src.pipeline.email.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get.return_value = mock_response_list
            client.post.return_value = mock_response_create
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client
            token = await pm.ensure_server("test-project")
            assert token == "srv-token-abc"
            client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_server_reuses_existing(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Servers": [{"ID": 99, "Name": "sod-test-project", "ApiTokens": ["existing-token"]}]
        }
        mock_response.raise_for_status = MagicMock()
        with patch("src.pipeline.email.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get.return_value = mock_response
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client
            token = await pm.ensure_server("test-project")
            assert token == "existing-token"
            client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_push_templates_creates_new(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")
        templates = [EmailTemplate(alias="welcome", name="Welcome", subject="Welcome {{name}}", html_body="<h1>Hi</h1>", text_body="Hi")]
        mock_list = MagicMock()
        mock_list.json.return_value = {"Templates": []}
        mock_list.raise_for_status = MagicMock()
        mock_create = MagicMock()
        mock_create.raise_for_status = MagicMock()
        mock_create.json.return_value = {"TemplateId": 1, "Alias": "welcome"}
        with patch("src.pipeline.email.httpx.AsyncClient") as MockClient:
            client = AsyncMock()
            client.get.return_value = mock_list
            client.post.return_value = mock_create
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = client
            await pm.push_templates("srv-token", templates)
            client.post.assert_called_once()


class TestParsedEmail:
    def test_create(self):
        from src.pipeline.email import ParsedEmail
        e = ParsedEmail(
            subject="Welcome", body_html="<p>Hi</p>", body_text="Hi",
            links=["https://app.example.com/verify?token=abc"],
            from_addr="noreply@example.com", to_addr="test@millionlabs.digital",
        )
        assert e.subject == "Welcome"
        assert len(e.links) == 1


class TestInboxReader:
    def test_import(self):
        from src.pipeline.email import InboxReader
        reader = InboxReader(host="mail.privateemail.com", port=993, user="hello@millionlabs.digital", password="testpass")
        assert reader.host == "mail.privateemail.com"

    def test_extract_verification_link(self):
        from src.pipeline.email import InboxReader, ParsedEmail
        reader = InboxReader(host="mail.privateemail.com", port=993, user="hello@millionlabs.digital", password="testpass")
        email = ParsedEmail(
            subject="Verify your email",
            body_html='<a href="https://myapp.fly.dev/verify?token=abc123">Verify</a>',
            body_text="Verify: https://myapp.fly.dev/verify?token=abc123",
            links=["https://myapp.fly.dev/verify?token=abc123"],
            from_addr="noreply@myapp.fly.dev", to_addr="test@millionlabs.digital",
        )
        link = reader.extract_verification_link(email, r"myapp\.fly\.dev")
        assert link == "https://myapp.fly.dev/verify?token=abc123"

    def test_extract_verification_link_no_match(self):
        from src.pipeline.email import InboxReader, ParsedEmail
        reader = InboxReader(host="mail.privateemail.com", port=993, user="hello@millionlabs.digital", password="testpass")
        email = ParsedEmail(subject="Test", body_html="<p>No links</p>", body_text="No links", links=[], from_addr="x@y.com", to_addr="t@millionlabs.digital")
        link = reader.extract_verification_link(email, r"myapp\.fly\.dev")
        assert link is None

    def test_extract_links_from_html(self):
        from src.pipeline.email import InboxReader
        html = '<a href="https://app.fly.dev/verify?t=1">V</a><a href="https://app.fly.dev/reset?t=2">R</a>'
        links = InboxReader._extract_links_from_html(html)
        assert len(links) == 2
        assert "https://app.fly.dev/verify?t=1" in links
        assert "https://app.fly.dev/reset?t=2" in links


class TestParseArchitectureTemplates:
    def test_parse_templates_section(self):
        from src.pipeline.email import parse_architecture_templates
        content = "# Architecture\n\n## Email Templates\n- alias: welcome | subject: Welcome to {{app_name}} | trigger: after signup | variables: app_name, name\n- alias: password-reset | subject: Reset your password | trigger: forgot password | variables: reset_link\n- alias: invite | subject: You've been invited | trigger: team invite | variables: inviter_name, invite_link\n\n## Data Models\nSome models here.\n"
        templates = parse_architecture_templates(content)
        assert len(templates) == 3
        assert templates[0]["alias"] == "welcome"
        assert templates[0]["subject"] == "Welcome to {{app_name}}"
        assert templates[1]["alias"] == "password-reset"
        assert templates[2]["alias"] == "invite"

    def test_parse_no_templates_section(self):
        from src.pipeline.email import parse_architecture_templates
        content = "# Architecture\n\n## Tech Stack\nJust a regular architecture doc.\n\n## Data Models\nSome models.\n"
        templates = parse_architecture_templates(content)
        assert templates == []

    def test_parse_empty_templates_section(self):
        from src.pipeline.email import parse_architecture_templates
        content = "# Architecture\n\n## Email Templates\n\n## Data Models\n"
        templates = parse_architecture_templates(content)
        assert templates == []


class TestLoadTemplateFiles:
    def test_load_matching_files(self, tmp_path: Path):
        from src.pipeline.email import load_template_files
        emails_dir = tmp_path / "emails"
        emails_dir.mkdir()
        (emails_dir / "welcome.html").write_text("<h1>Welcome {{name}}</h1>")
        (emails_dir / "welcome.txt").write_text("Welcome {{name}}")
        specs = [{"alias": "welcome", "subject": "Welcome {{name}}"}]
        templates = load_template_files(str(tmp_path), specs)
        assert len(templates) == 1
        assert templates[0].alias == "welcome"
        assert templates[0].html_body == "<h1>Welcome {{name}}</h1>"
        assert templates[0].text_body == "Welcome {{name}}"

    def test_missing_html_skipped(self, tmp_path: Path):
        from src.pipeline.email import load_template_files
        specs = [{"alias": "missing", "subject": "Test"}]
        templates = load_template_files(str(tmp_path), specs)
        assert templates == []
