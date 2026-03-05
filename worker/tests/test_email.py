"""Tests for email integration module."""
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
