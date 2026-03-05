# Email Testing & Integration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Postmark template management and IMAP-based email verification to the Deploy and E2E Testing phases.

**Architecture:** New `email.py` utility module with `PostmarkManager` (Postmark Server + template API) and `InboxReader` (IMAP polling + link extraction). Deploy phase pushes templates after app deploy. E2E phase uses IMAP to verify delivery, extracts links, hands them to Playwright. See `docs/plans/2026-03-05-email-testing-integration-design.md` for full design.

**Tech Stack:** Python `imaplib` (stdlib), `httpx` (already a dependency), `email` (stdlib for parsing), Postmark Account API, Private Email IMAP (`mail.privateemail.com:993`)

---

### Task 1: Add EmailTemplate dataclass to models.py

**Files:**
- Modify: `worker/src/pipeline/models.py`
- Test: `worker/tests/test_email.py`

**Step 1: Write the failing test**

```python
# worker/tests/test_email.py
"""Tests for email integration module."""
from src.pipeline.models import EmailTemplate


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
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_email.py::TestEmailTemplate -v`
Expected: FAIL with `ImportError: cannot import name 'EmailTemplate'`

**Step 3: Write minimal implementation**

Add to `worker/src/pipeline/models.py` after the existing `BuildPlan` class:

```python
@dataclass
class EmailTemplate:
    alias: str          # e.g., "welcome", "password-reset"
    name: str           # Human-readable name
    subject: str        # Subject line with {{variables}}
    html_body: str      # HTML template
    text_body: str      # Plain text fallback
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_email.py::TestEmailTemplate -v`
Expected: PASS

**Step 5: Commit**

```bash
git add worker/src/pipeline/models.py worker/tests/test_email.py
git commit -m "feat: add EmailTemplate dataclass"
```

---

### Task 2: Add email config fields to Config

**Files:**
- Modify: `worker/src/config.py`
- Test: `worker/tests/test_email.py`

**Step 1: Write the failing test**

Append to `worker/tests/test_email.py`:

```python
import os
from src.config import Config


class TestEmailConfig:
    def test_config_has_email_fields(self):
        """Config should have Postmark and Private Email fields with defaults."""
        # Build a minimal config with required fields
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
        """from_env() should read email fields from environment."""
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
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_email.py::TestEmailConfig -v`
Expected: FAIL with `unexpected keyword argument 'postmark_account_api_key'` or `has no attribute`

**Step 3: Write minimal implementation**

In `worker/src/config.py`, add these fields after `resend_api_key`:

```python
    # Email testing (Postmark + Private Email IMAP)
    postmark_account_api_key: str = ""
    private_email_host: str = "mail.privateemail.com"
    private_email_user: str = ""
    private_email_password: str = ""
```

In `from_env()`, add these lines inside the `return cls(...)` call, after the `resend_api_key` line:

```python
            postmark_account_api_key=os.environ.get("POSTMARK_ACCOUNT_API_KEY", ""),
            private_email_host=os.environ.get("PRIVATE_EMAIL_HOST", "mail.privateemail.com"),
            private_email_user=os.environ.get("PRIVATE_EMAIL_USER", ""),
            private_email_password=os.environ.get("PRIVATE_EMAIL_PASSWORD", ""),
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_email.py::TestEmailConfig -v`
Expected: PASS

**Step 5: Commit**

```bash
git add worker/src/config.py worker/tests/test_email.py
git commit -m "feat: add email config fields for Postmark and Private Email"
```

---

### Task 3: Create PostmarkManager

**Files:**
- Create: `worker/src/pipeline/email.py`
- Test: `worker/tests/test_email.py`

**Step 1: Write the failing tests**

Append to `worker/tests/test_email.py`:

```python
import json
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from src.pipeline.models import EmailTemplate


class TestPostmarkManager:
    def test_import(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")
        assert pm.account_api_key == "test-key"

    @pytest.mark.asyncio
    async def test_ensure_server_creates_new(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")

        # Mock: list servers returns empty, then create returns new server
        mock_response_list = MagicMock()
        mock_response_list.json.return_value = {"Servers": []}
        mock_response_list.raise_for_status = MagicMock()

        mock_response_create = MagicMock()
        mock_response_create.json.return_value = {
            "ID": 12345,
            "ApiTokens": ["srv-token-abc"],
        }
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
            "Servers": [
                {"ID": 99, "Name": "sod-test-project", "ApiTokens": ["existing-token"]}
            ]
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
    async def test_push_templates_upserts(self):
        from src.pipeline.email import PostmarkManager
        pm = PostmarkManager(account_api_key="test-key")

        templates = [
            EmailTemplate(
                alias="welcome",
                name="Welcome",
                subject="Welcome {{name}}",
                html_body="<h1>Hi</h1>",
                text_body="Hi",
            ),
        ]

        # Mock: list templates returns empty (no existing), then create succeeds
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
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_email.py::TestPostmarkManager -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline.email'`

**Step 3: Write minimal implementation**

Create `worker/src/pipeline/email.py`:

```python
"""Email integration — Postmark template management and IMAP inbox reading."""
from __future__ import annotations

import httpx

from src.pipeline.models import EmailTemplate

# Postmark API base URL
_POSTMARK_ACCOUNT_API = "https://api.postmarkapp.com"


class PostmarkManager:
    """Manages Postmark Servers and templates via the Account API."""

    def __init__(self, account_api_key: str) -> None:
        self.account_api_key = account_api_key

    def _account_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Account-Token": self.account_api_key,
        }

    def _server_headers(self, server_token: str) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": server_token,
        }

    async def ensure_server(self, project_name: str) -> str:
        """Create or retrieve a Postmark Server. Returns the server API token."""
        server_name = f"sod-{project_name}"

        async with httpx.AsyncClient() as client:
            # Search for existing server
            resp = await client.get(
                f"{_POSTMARK_ACCOUNT_API}/servers",
                headers=self._account_headers(),
                params={"count": 100, "offset": 0, "name": server_name},
            )
            resp.raise_for_status()
            servers = resp.json().get("Servers", [])

            for srv in servers:
                if srv["Name"] == server_name:
                    tokens = srv.get("ApiTokens", [])
                    if tokens:
                        print(f"[email] Reusing Postmark Server '{server_name}' (ID={srv['ID']})")
                        return tokens[0]

            # Create new server
            resp = await client.post(
                f"{_POSTMARK_ACCOUNT_API}/servers",
                headers=self._account_headers(),
                json={"Name": server_name, "DeliveryType": "Live"},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data["ApiTokens"][0]
            print(f"[email] Created Postmark Server '{server_name}' (ID={data['ID']})")
            return token

    async def push_templates(
        self, server_token: str, templates: list[EmailTemplate]
    ) -> None:
        """Push templates to a Postmark Server. Upserts by alias."""
        headers = self._server_headers(server_token)

        async with httpx.AsyncClient() as client:
            # Get existing templates
            resp = await client.get(
                f"{_POSTMARK_ACCOUNT_API}/templates",
                headers=headers,
                params={"count": 100, "offset": 0},
            )
            resp.raise_for_status()
            existing = {
                t["Alias"]: t["TemplateId"]
                for t in resp.json().get("Templates", [])
                if t.get("Alias")
            }

            for tmpl in templates:
                payload = {
                    "Alias": tmpl.alias,
                    "Name": tmpl.name,
                    "Subject": tmpl.subject,
                    "HtmlBody": tmpl.html_body,
                    "TextBody": tmpl.text_body,
                }

                if tmpl.alias in existing:
                    # Update existing template
                    tid = existing[tmpl.alias]
                    await client.put(
                        f"{_POSTMARK_ACCOUNT_API}/templates/{tid}",
                        headers=headers,
                        json=payload,
                    )
                    print(f"[email] Updated template '{tmpl.alias}' (ID={tid})")
                else:
                    # Create new template
                    resp = await client.post(
                        f"{_POSTMARK_ACCOUNT_API}/templates",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    print(f"[email] Created template '{tmpl.alias}'")
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_email.py::TestPostmarkManager -v`
Expected: PASS

**Step 5: Run all existing tests to check for regressions**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add worker/src/pipeline/email.py worker/tests/test_email.py
git commit -m "feat: add PostmarkManager for server and template management"
```

---

### Task 4: Create InboxReader

**Files:**
- Modify: `worker/src/pipeline/email.py`
- Test: `worker/tests/test_email.py`

**Step 1: Write the failing tests**

Append to `worker/tests/test_email.py`:

```python
from src.pipeline.email import InboxReader, ParsedEmail


class TestParsedEmail:
    def test_create(self):
        e = ParsedEmail(
            subject="Welcome",
            body_html="<p>Hi</p>",
            body_text="Hi",
            links=["https://app.example.com/verify?token=abc"],
            from_addr="noreply@example.com",
            to_addr="test@millionlabs.digital",
        )
        assert e.subject == "Welcome"
        assert len(e.links) == 1


class TestInboxReader:
    def test_import(self):
        reader = InboxReader(
            host="mail.privateemail.com",
            port=993,
            user="hello@millionlabs.digital",
            password="testpass",
        )
        assert reader.host == "mail.privateemail.com"

    def test_extract_verification_link(self):
        reader = InboxReader(
            host="mail.privateemail.com",
            port=993,
            user="hello@millionlabs.digital",
            password="testpass",
        )
        email = ParsedEmail(
            subject="Verify your email",
            body_html='<a href="https://myapp.fly.dev/verify?token=abc123">Verify</a>',
            body_text="Verify: https://myapp.fly.dev/verify?token=abc123",
            links=["https://myapp.fly.dev/verify?token=abc123"],
            from_addr="noreply@myapp.fly.dev",
            to_addr="test@millionlabs.digital",
        )
        link = reader.extract_verification_link(email, r"myapp\.fly\.dev")
        assert link == "https://myapp.fly.dev/verify?token=abc123"

    def test_extract_verification_link_no_match(self):
        reader = InboxReader(
            host="mail.privateemail.com",
            port=993,
            user="hello@millionlabs.digital",
            password="testpass",
        )
        email = ParsedEmail(
            subject="Test",
            body_html="<p>No links</p>",
            body_text="No links",
            links=[],
            from_addr="x@y.com",
            to_addr="t@millionlabs.digital",
        )
        link = reader.extract_verification_link(email, r"myapp\.fly\.dev")
        assert link is None

    def test_parse_email_body_extracts_links(self):
        """_parse_email_body should extract all href links from HTML."""
        from src.pipeline.email import InboxReader
        html = '''
        <html><body>
        <a href="https://app.fly.dev/verify?t=1">Verify</a>
        <a href="https://app.fly.dev/reset?t=2">Reset</a>
        <p>No link here</p>
        </body></html>
        '''
        links = InboxReader._extract_links_from_html(html)
        assert len(links) == 2
        assert "https://app.fly.dev/verify?t=1" in links
        assert "https://app.fly.dev/reset?t=2" in links
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_email.py::TestInboxReader tests/test_email.py::TestParsedEmail -v`
Expected: FAIL with `ImportError`

**Step 3: Write minimal implementation**

Add to `worker/src/pipeline/email.py`:

```python
import imaplib
import email as email_lib
from email.header import decode_header
from dataclasses import dataclass, field
import re
import time


@dataclass
class ParsedEmail:
    subject: str
    body_html: str
    body_text: str
    links: list[str]
    from_addr: str
    to_addr: str


class InboxReader:
    """IMAP client for reading test emails from Private Email."""

    def __init__(self, host: str, port: int, user: str, password: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password

    @staticmethod
    def _extract_links_from_html(html: str) -> list[str]:
        """Extract all href URLs from HTML content."""
        return re.findall(r'href=["\']([^"\']+)["\']', html)

    @staticmethod
    def _decode_subject(msg: email_lib.message.Message) -> str:
        raw = msg.get("Subject", "")
        parts = decode_header(raw)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return " ".join(decoded)

    @staticmethod
    def _get_body(msg: email_lib.message.Message) -> tuple[str, str]:
        """Extract text and HTML body from an email message."""
        text_body = ""
        html_body = ""

        if msg.is_multipart():
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain" and not text_body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        text_body = payload.decode(errors="replace")
                elif ct == "text/html" and not html_body:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_body = payload.decode(errors="replace")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                content = payload.decode(errors="replace")
                if msg.get_content_type() == "text/html":
                    html_body = content
                else:
                    text_body = content

        return text_body, html_body

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL(self.host, self.port)
        conn.login(self.user, self.password)
        return conn

    def wait_for_email(
        self,
        recipient: str,
        subject_pattern: str,
        timeout: int = 60,
    ) -> ParsedEmail | None:
        """Poll IMAP for an email matching recipient and subject pattern.

        Uses exponential backoff: 2s, 4s, 8s, 16s (capped).
        Returns None if timeout is reached.
        """
        deadline = time.monotonic() + timeout
        delay = 2.0
        max_delay = 16.0

        while time.monotonic() < deadline:
            result = self._search_email(recipient, subject_pattern)
            if result:
                return result

            remaining = deadline - time.monotonic()
            sleep_time = min(delay, remaining, max_delay)
            if sleep_time <= 0:
                break
            time.sleep(sleep_time)
            delay = min(delay * 2, max_delay)

        print(
            f"[email] Timeout: no email for {recipient} "
            f"matching '{subject_pattern}' within {timeout}s"
        )
        return None

    def _search_email(
        self, recipient: str, subject_pattern: str
    ) -> ParsedEmail | None:
        """Search IMAP inbox for a matching email."""
        try:
            conn = self._connect()
            conn.select("INBOX")

            # Search by TO header
            _, msg_nums = conn.search(None, f'(TO "{recipient}")')
            if not msg_nums or not msg_nums[0]:
                conn.logout()
                return None

            pattern = re.compile(subject_pattern, re.IGNORECASE)

            # Check messages in reverse order (newest first)
            nums = msg_nums[0].split()
            for num in reversed(nums):
                _, data = conn.fetch(num, "(RFC822)")
                if not data or not data[0]:
                    continue
                raw = data[0][1]
                msg = email_lib.message_from_bytes(raw)

                subject = self._decode_subject(msg)
                if not pattern.search(subject):
                    continue

                text_body, html_body = self._get_body(msg)
                links = self._extract_links_from_html(html_body) if html_body else []

                conn.logout()
                return ParsedEmail(
                    subject=subject,
                    body_html=html_body,
                    body_text=text_body,
                    links=links,
                    from_addr=msg.get("From", ""),
                    to_addr=recipient,
                )

            conn.logout()
        except Exception as e:
            print(f"[email] IMAP search error: {e}")

        return None

    def extract_verification_link(
        self, email: ParsedEmail, link_pattern: str
    ) -> str | None:
        """Find the first link matching the pattern."""
        pattern = re.compile(link_pattern)
        for link in email.links:
            if pattern.search(link):
                return link
        return None

    def clear_inbox(self, recipient: str) -> int:
        """Delete all emails addressed to the given recipient. Returns count deleted."""
        try:
            conn = self._connect()
            conn.select("INBOX")

            _, msg_nums = conn.search(None, f'(TO "{recipient}")')
            if not msg_nums or not msg_nums[0]:
                conn.logout()
                return 0

            nums = msg_nums[0].split()
            for num in nums:
                conn.store(num, "+FLAGS", "\\Deleted")
            conn.expunge()
            conn.logout()
            print(f"[email] Cleared {len(nums)} emails for {recipient}")
            return len(nums)
        except Exception as e:
            print(f"[email] Failed to clear inbox for {recipient}: {e}")
            return 0
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_email.py::TestInboxReader tests/test_email.py::TestParsedEmail -v`
Expected: PASS

**Step 5: Run all tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add worker/src/pipeline/email.py worker/tests/test_email.py
git commit -m "feat: add InboxReader for IMAP email verification"
```

---

### Task 5: Add template parsing helper

**Files:**
- Modify: `worker/src/pipeline/email.py`
- Test: `worker/tests/test_email.py`

**Step 1: Write the failing test**

Append to `worker/tests/test_email.py`:

```python
class TestParseArchitectureTemplates:
    def test_parse_templates_section(self):
        from src.pipeline.email import parse_architecture_templates

        content = """# Architecture

## Tech Stack
Some tech info here.

## Email Templates
- alias: welcome | subject: Welcome to {{app_name}} | trigger: after signup | variables: app_name, name
- alias: password-reset | subject: Reset your password | trigger: forgot password | variables: reset_link
- alias: invite | subject: You've been invited | trigger: team invite | variables: inviter_name, invite_link

## Data Models
Some models here.
"""
        templates = parse_architecture_templates(content)
        assert len(templates) == 3
        assert templates[0]["alias"] == "welcome"
        assert templates[0]["subject"] == "Welcome to {{app_name}}"
        assert templates[1]["alias"] == "password-reset"
        assert templates[2]["alias"] == "invite"

    def test_parse_no_templates_section(self):
        from src.pipeline.email import parse_architecture_templates

        content = """# Architecture

## Tech Stack
Just a regular architecture doc.

## Data Models
Some models.
"""
        templates = parse_architecture_templates(content)
        assert templates == []

    def test_parse_empty_templates_section(self):
        from src.pipeline.email import parse_architecture_templates

        content = """# Architecture

## Email Templates

## Data Models
"""
        templates = parse_architecture_templates(content)
        assert templates == []
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_email.py::TestParseArchitectureTemplates -v`
Expected: FAIL with `ImportError: cannot import name 'parse_architecture_templates'`

**Step 3: Write minimal implementation**

Add to `worker/src/pipeline/email.py`:

```python
def parse_architecture_templates(content: str) -> list[dict[str, str]]:
    """Parse the '## Email Templates' section from ARCHITECTURE.md.

    Expected format per line:
        - alias: <alias> | subject: <subject> | trigger: <trigger> | variables: <vars>

    Returns a list of dicts with keys: alias, subject, trigger, variables.
    Returns empty list if no Email Templates section found.
    """
    # Find the Email Templates section
    match = re.search(
        r"^## Email Templates\s*\n(.*?)(?=^## |\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []

    section = match.group(1).strip()
    if not section:
        return []

    templates: list[dict[str, str]] = []
    for line in section.split("\n"):
        line = line.strip()
        if not line.startswith("- alias:"):
            continue

        # Parse pipe-separated fields
        parts = line.lstrip("- ").split("|")
        entry: dict[str, str] = {}
        for part in parts:
            part = part.strip()
            if ":" in part:
                key, _, value = part.partition(":")
                entry[key.strip()] = value.strip()

        if "alias" in entry:
            templates.append(entry)

    return templates
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_email.py::TestParseArchitectureTemplates -v`
Expected: PASS

**Step 5: Commit**

```bash
git add worker/src/pipeline/email.py worker/tests/test_email.py
git commit -m "feat: add ARCHITECTURE.md email template parser"
```

---

### Task 6: Update architect prompt to identify email touchpoints

**Files:**
- Modify: `worker/src/prompts/planning.py`

**Step 1: Read current file**

File: `worker/src/prompts/planning.py`

**Step 2: Add email template instructions to architecture_prompt**

In `worker/src/prompts/planning.py`, append the following to the `architecture_prompt` function's return string, before the closing `"""`:

```python
## Email Templates

When designing the system, identify all email touchpoints. If the PRD requires any email functionality
(registration verification, password reset, notifications, invitations, etc.), document each email in
ARCHITECTURE.md under a section called "## Email Templates" using this exact format:

- alias: <template-alias> | subject: <subject line with {{variables}}> | trigger: <what causes this email> | variables: <comma-separated list of template variables>

Examples:
- alias: welcome | subject: Welcome to {{app_name}} | trigger: after signup | variables: app_name, name, verify_link
- alias: password-reset | subject: Reset your password | trigger: forgot password flow | variables: name, reset_link
- alias: invite | subject: You've been invited to {{app_name}} | trigger: team invite | variables: inviter_name, app_name, invite_link

If the PRD has no email requirements, omit this section entirely.
```

**Step 3: Run all tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS (prompt changes don't break anything)

**Step 4: Commit**

```bash
git add worker/src/prompts/planning.py
git commit -m "feat: expand architect prompt to identify email templates"
```

---

### Task 7: Update testing prompts for IMAP-based email verification

**Files:**
- Modify: `worker/src/prompts/testing.py`

**Step 1: Read current file and identify changes**

File: `worker/src/prompts/testing.py`

Three areas to update:
1. `user_flows_instructions()` — update `check_email` action to reference IMAP (remove Resend reference)
2. `seed_data_instructions()` — update email pattern from `<role>@test.mlabs.app` to `{job_id}-{role}@millionlabs.digital`
3. `e2e_batch_tester_prompt()` — replace Resend curl check_email with a note that email verification is handled by the orchestrator (IMAP)

**Step 2: Update user_flows_instructions**

In `user_flows_instructions()`, change the `check_email` action description from:
```
- **check_email**: Read sent email via Resend API. Params: `to`, `subject_contains`, `timeout`, `extract` (variable name for extracted URL)
```
To:
```
- **check_email**: Verify email delivery via IMAP. Params: `to`, `subject_contains`, `timeout`, `extract` (variable name for extracted URL). The orchestrator reads the email via IMAP and extracts links automatically.
```

Change the email address pattern references from `<role>@test.mlabs.app` to `<role>@millionlabs.digital` in both `user_flows_instructions()` and `seed_data_instructions()`.

**Step 3: Update e2e_batch_tester_prompt**

In `e2e_batch_tester_prompt()`, replace the `check_email` curl section:
```
   - `check_email`: Use curl to call Resend API:
     ```bash
     curl -s https://api.resend.com/emails \
       -H "Authorization: Bearer {resend_api_key}" \
       -H "Content-Type: application/json"
     ```
```
With:
```
   - `check_email`: Email verification is handled by the orchestrator via IMAP.
     Write the flow step result as PENDING_EMAIL with the expected recipient and subject.
     The orchestrator will poll the inbox and provide the verification link.
     If you need to manually check, the email will arrive at the `{job_id}-{role}@millionlabs.digital` inbox.
```

Also update the same in `e2e_tester_prompt()` for consistency.

**Step 4: Run all tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add worker/src/prompts/testing.py
git commit -m "feat: update E2E prompts for IMAP-based email verification"
```

---

### Task 8: Integrate PostmarkManager into deployer

**Files:**
- Modify: `worker/src/pipeline/deployer.py`
- Test: `worker/tests/test_email.py`

**Step 1: Write the failing test**

Append to `worker/tests/test_email.py`:

```python
class TestDeployerEmailIntegration:
    def test_parse_and_load_templates(self, tmp_path: Path):
        """Deployer should parse ARCHITECTURE.md and load template files."""
        from src.pipeline.email import parse_architecture_templates, load_template_files

        # Create architecture doc
        arch = tmp_path / "docs" / "ARCHITECTURE.md"
        arch.parent.mkdir(parents=True)
        arch.write_text(
            "# Architecture\n\n"
            "## Email Templates\n"
            "- alias: welcome | subject: Welcome {{name}} | trigger: signup | variables: name\n\n"
            "## Data Models\n"
        )

        # Create template files
        emails_dir = tmp_path / "emails"
        emails_dir.mkdir()
        (emails_dir / "welcome.html").write_text("<h1>Welcome {{name}}</h1>")
        (emails_dir / "welcome.txt").write_text("Welcome {{name}}")

        specs = parse_architecture_templates(arch.read_text())
        assert len(specs) == 1

        templates = load_template_files(str(tmp_path), specs)
        assert len(templates) == 1
        assert templates[0].alias == "welcome"
        assert templates[0].html_body == "<h1>Welcome {{name}}</h1>"
        assert templates[0].text_body == "Welcome {{name}}"

    def test_load_template_files_missing_html(self, tmp_path: Path):
        """Missing template files should be skipped with a warning."""
        from src.pipeline.email import load_template_files

        specs = [{"alias": "missing", "subject": "Test"}]
        templates = load_template_files(str(tmp_path), specs)
        assert templates == []
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_email.py::TestDeployerEmailIntegration -v`
Expected: FAIL with `ImportError: cannot import name 'load_template_files'`

**Step 3: Add load_template_files to email.py**

Add to `worker/src/pipeline/email.py`:

```python
from pathlib import Path as _Path

def load_template_files(
    repo_path: str, template_specs: list[dict[str, str]]
) -> list[EmailTemplate]:
    """Load template HTML/text files from emails/ directory.

    Matches each spec's alias to emails/<alias>.html and emails/<alias>.txt.
    Skips templates with missing files.
    """
    emails_dir = _Path(repo_path) / "emails"
    templates: list[EmailTemplate] = []

    for spec in template_specs:
        alias = spec["alias"]
        html_file = emails_dir / f"{alias}.html"
        txt_file = emails_dir / f"{alias}.txt"

        if not html_file.exists():
            print(f"[email] Warning: missing template file {html_file}")
            continue

        html_body = html_file.read_text()
        text_body = txt_file.read_text() if txt_file.exists() else ""

        templates.append(EmailTemplate(
            alias=alias,
            name=spec.get("name", alias.replace("-", " ").title()),
            subject=spec.get("subject", alias),
            html_body=html_body,
            text_body=text_body,
        ))

    return templates
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_email.py::TestDeployerEmailIntegration -v`
Expected: PASS

**Step 5: Integrate into deployer.py**

In `worker/src/pipeline/deployer.py`, add after the existing deploy verification step (after Step 5, before Step 6 commit):

```python
    # --- Step 5b: Push email templates to Postmark (if configured) ---
    if config.postmark_account_api_key and fly_app_name:
        arch_path = Path(repo_path) / "docs" / "ARCHITECTURE.md"
        if arch_path.exists():
            from src.pipeline.email import (
                PostmarkManager,
                parse_architecture_templates,
                load_template_files,
            )
            template_specs = parse_architecture_templates(arch_path.read_text())
            if template_specs:
                print(f"[deployer] Found {len(template_specs)} email templates — pushing to Postmark")
                await reporter.report("email_setup_started", {
                    "template_count": len(template_specs),
                })

                pm = PostmarkManager(config.postmark_account_api_key)
                server_token = await pm.ensure_server(config.job_id[:8])

                templates = load_template_files(repo_path, template_specs)
                if templates:
                    await pm.push_templates(server_token, templates)

                    # Set Postmark server token as Fly secret
                    subprocess.run(
                        [
                            "flyctl", "secrets", "set",
                            f"POSTMARK_SERVER_TOKEN={server_token}",
                            "-a", fly_app_name,
                        ],
                        capture_output=True, text=True, timeout=30,
                    )
                    print(f"[deployer] Set POSTMARK_SERVER_TOKEN on {fly_app_name}")

                await reporter.report("email_setup_complete", {
                    "templates_pushed": len(templates),
                    "server_name": f"sod-{config.job_id[:8]}",
                })
            else:
                print("[deployer] No email templates in ARCHITECTURE.md — skipping Postmark setup")
```

Import `subprocess` is already imported at the top of deployer.py.

**Step 6: Run all tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 7: Commit**

```bash
git add worker/src/pipeline/email.py worker/src/pipeline/deployer.py worker/tests/test_email.py
git commit -m "feat: integrate Postmark template push into deploy phase"
```

---

### Task 9: Integrate InboxReader into E2E tester

**Files:**
- Modify: `worker/src/pipeline/tester.py`

**Step 1: Read the current tester.py integration points**

The key integration is in `run_e2e_tests()`. Before each batch, we need to:
1. Clear the inbox for this job's email addresses
2. After the batch, check if any flows reported `PENDING_EMAIL` status

However, the simpler and more robust approach: make InboxReader available as a utility that the deployer has set up, and update the batch tester prompt to tell the agent how to use the IMAP-checked emails.

Actually, looking at the architecture more carefully, the cleanest integration is:

**In `run_e2e_tests()`**, before the batch loop starts:
- Initialize InboxReader if email config is present
- Clear inbox for `{job_id}-*` recipients

**Pass email config to the batch prompt** so the agent knows:
- Test email addresses use `{job_id}-{role}@millionlabs.digital`
- Email verification is handled between batch steps by the orchestrator

**Step 2: Modify tester.py**

At the top of `run_e2e_tests()`, after the preflight check, add inbox clearing:

```python
    # Clear email inbox for this job if email testing is configured
    email_reader = None
    if config.private_email_user and config.private_email_password:
        from src.pipeline.email import InboxReader
        email_reader = InboxReader(
            host=config.private_email_host,
            port=993,
            user=config.private_email_user,
            password=config.private_email_password,
        )
        # Clear any stale emails for this job's test addresses
        email_reader.clear_inbox(f"{config.job_id[:8]}")
        print(f"[tester] Email inbox cleared for job {config.job_id[:8]}")
```

**Step 3: Update e2e_batch_tester_prompt signature**

In `worker/src/prompts/testing.py`, update `e2e_batch_tester_prompt()` to accept a `job_id` parameter and include the email addressing pattern:

Add parameter: `job_id: str = ""`

Add to the prompt body (after Test Credentials section):

```
## Email Testing

Test emails use the address pattern: {job_id}-<role>@millionlabs.digital
For example: {job_id}-admin@millionlabs.digital, {job_id}-user@millionlabs.digital

For flows with check_email steps:
1. Use the test email address above when the flow requires entering an email
2. After triggering an email-sending action, wait 10 seconds
3. The orchestrator will verify email delivery via IMAP between batches
4. To verify inline: use the email check endpoint if the app exposes one
```

**Step 4: Update the prompt call in tester.py**

In `run_e2e_tests()`, update the `e2e_batch_tester_prompt()` call to pass `job_id=config.job_id[:8]`.

**Step 5: Run all tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add worker/src/pipeline/tester.py worker/src/prompts/testing.py
git commit -m "feat: integrate InboxReader into E2E testing phase"
```

---

### Task 10: Add verify_email_setup to PhaseVerifier

**Files:**
- Modify: `worker/src/orchestrator/verifier.py`
- Test: `worker/tests/test_verifier.py`

**Step 1: Write the failing test**

Append to `worker/tests/test_verifier.py`:

```python
class TestVerifyEmailSetup:
    def test_no_email_templates_passes(self, tmp_path: Path):
        """Projects without email templates should pass email verification."""
        _write(tmp_path / "docs" / "ARCHITECTURE.md", "# Architecture\n## Data Models\n")
        from src.orchestrator.verifier import PhaseVerifier
        v = PhaseVerifier(tmp_path)
        result = v.verify_email_setup()
        assert result.passed is True

    def test_templates_with_matching_files_passes(self, tmp_path: Path):
        _write(
            tmp_path / "docs" / "ARCHITECTURE.md",
            "# Architecture\n\n## Email Templates\n"
            "- alias: welcome | subject: Hi | trigger: signup | variables: name\n\n"
            "## Other\n",
        )
        _write(tmp_path / "emails" / "welcome.html", "<h1>Hi {{name}}</h1>")
        _write(tmp_path / "emails" / "welcome.txt", "Hi {{name}}")

        from src.orchestrator.verifier import PhaseVerifier
        v = PhaseVerifier(tmp_path)
        result = v.verify_email_setup()
        assert result.passed is True

    def test_templates_missing_files_fails(self, tmp_path: Path):
        _write(
            tmp_path / "docs" / "ARCHITECTURE.md",
            "# Architecture\n\n## Email Templates\n"
            "- alias: welcome | subject: Hi | trigger: signup | variables: name\n\n"
            "## Other\n",
        )
        # No emails/ directory

        from src.orchestrator.verifier import PhaseVerifier
        v = PhaseVerifier(tmp_path)
        result = v.verify_email_setup()
        assert result.passed is False
        assert any("welcome" in issue for issue in result.issues)
```

**Step 2: Run test to verify it fails**

Run: `cd worker && python -m pytest tests/test_verifier.py::TestVerifyEmailSetup -v`
Expected: FAIL with `AttributeError: 'PhaseVerifier' object has no attribute 'verify_email_setup'`

**Step 3: Write minimal implementation**

Add to `PhaseVerifier` in `worker/src/orchestrator/verifier.py`:

```python
    def verify_email_setup(self) -> VerifyResult:
        """Check email template files match ARCHITECTURE.md specs."""
        from src.pipeline.email import parse_architecture_templates

        arch_path = self.root / "docs" / "ARCHITECTURE.md"
        if not arch_path.is_file():
            return VerifyResult(passed=True)  # No architecture = nothing to check

        content = arch_path.read_text(errors="replace")
        template_specs = parse_architecture_templates(content)

        if not template_specs:
            return VerifyResult(passed=True)  # No email templates = skip

        issues: list[str] = []
        emails_dir = self.root / "emails"

        for spec in template_specs:
            alias = spec["alias"]
            html_file = emails_dir / f"{alias}.html"
            txt_file = emails_dir / f"{alias}.txt"

            if not html_file.exists():
                issues.append(f"Missing email template: emails/{alias}.html")
            if not txt_file.exists():
                issues.append(f"Missing email template: emails/{alias}.txt")

        return VerifyResult(passed=len(issues) == 0, issues=issues)
```

Also add `"email_setup"` to the CLI choices in `main()` and the corresponding elif branch:

```python
    # In the choices list:
    choices=["architecture", "scaffold", "task", "review", "email_setup"],

    # Add elif:
    elif args.phase == "email_setup":
        result = verifier.verify_email_setup()
```

**Step 4: Run test to verify it passes**

Run: `cd worker && python -m pytest tests/test_verifier.py::TestVerifyEmailSetup -v`
Expected: PASS

**Step 5: Run all tests**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 6: Commit**

```bash
git add worker/src/orchestrator/verifier.py worker/tests/test_verifier.py
git commit -m "feat: add verify_email_setup to PhaseVerifier"
```

---

### Task 11: Final integration test and cleanup

**Files:**
- Test: `worker/tests/test_email.py`

**Step 1: Run full test suite**

Run: `cd worker && python -m pytest tests/ -v`
Expected: All PASS

**Step 2: Verify imports are clean**

Run: `cd worker && python -c "from src.pipeline.email import PostmarkManager, InboxReader, ParsedEmail, parse_architecture_templates, load_template_files; print('All imports OK')"`
Expected: `All imports OK`

**Step 3: Verify config integration**

Run: `cd worker && python -c "from src.config import Config; c = Config(job_id='t', repo_url='r', orchestrator_url='o', webhook_secret='s', anthropic_api_key='k', github_app_id='1', github_app_installation_id='2', github_app_private_key='-----BEGIN RSA PRIVATE KEY-----\ntest\n-----END RSA PRIVATE KEY-----'); print(f'postmark={c.postmark_account_api_key!r}, email_host={c.private_email_host!r}')"`
Expected: `postmark='', email_host='mail.privateemail.com'`

**Step 4: Commit any remaining changes**

```bash
git add -A
git commit -m "feat: email testing integration complete"
```
