"""Email integration — Postmark template management and IMAP inbox reading."""
from __future__ import annotations

import email as email_lib
import imaplib
import re
import time
from dataclasses import dataclass
from email.header import decode_header
from pathlib import Path as _Path

import httpx

from src.pipeline.models import EmailTemplate

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

    async def push_templates(self, server_token: str, templates: list[EmailTemplate]) -> None:
        """Push templates to a Postmark Server. Upserts by alias."""
        headers = self._server_headers(server_token)
        async with httpx.AsyncClient() as client:
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
                    "Alias": tmpl.alias, "Name": tmpl.name,
                    "Subject": tmpl.subject, "HtmlBody": tmpl.html_body,
                    "TextBody": tmpl.text_body,
                }
                if tmpl.alias in existing:
                    tid = existing[tmpl.alias]
                    await client.put(f"{_POSTMARK_ACCOUNT_API}/templates/{tid}", headers=headers, json=payload)
                    print(f"[email] Updated template '{tmpl.alias}' (ID={tid})")
                else:
                    resp = await client.post(f"{_POSTMARK_ACCOUNT_API}/templates", headers=headers, json=payload)
                    resp.raise_for_status()
                    print(f"[email] Created template '{tmpl.alias}'")


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

    def wait_for_email(self, recipient: str, subject_pattern: str, timeout: int = 60) -> ParsedEmail | None:
        """Poll IMAP for an email matching recipient and subject. Exponential backoff: 2s, 4s, 8s, 16s cap."""
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
        print(f"[email] Timeout: no email for {recipient} matching '{subject_pattern}' within {timeout}s")
        return None

    def _search_email(self, recipient: str, subject_pattern: str) -> ParsedEmail | None:
        try:
            conn = self._connect()
            conn.select("INBOX")
            _, msg_nums = conn.search(None, f'(TO "{recipient}")')
            if not msg_nums or not msg_nums[0]:
                conn.logout()
                return None
            pattern = re.compile(subject_pattern, re.IGNORECASE)
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
                    subject=subject, body_html=html_body, body_text=text_body,
                    links=links, from_addr=msg.get("From", ""), to_addr=recipient,
                )
            conn.logout()
        except Exception as e:
            print(f"[email] IMAP search error: {e}")
        return None

    def extract_verification_link(self, email: ParsedEmail, link_pattern: str) -> str | None:
        pattern = re.compile(link_pattern)
        for link in email.links:
            if pattern.search(link):
                return link
        return None

    def clear_inbox(self, recipient: str) -> int:
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


def parse_architecture_templates(content: str) -> list[dict[str, str]]:
    """Parse '## Email Templates' section from ARCHITECTURE.md.

    Format: - alias: <alias> | subject: <subject> | trigger: <trigger> | variables: <vars>
    Returns list of dicts. Empty list if no section found.
    """
    match = re.search(
        r"^## Email Templates\s*\n(.*?)(?=^## |\Z)",
        content, re.MULTILINE | re.DOTALL,
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


def load_template_files(repo_path: str, template_specs: list[dict[str, str]]) -> list[EmailTemplate]:
    """Load template HTML/text files from emails/ directory. Skips templates with missing files."""
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
