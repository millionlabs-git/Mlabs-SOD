# Email Testing & Integration Design

**Date:** 2026-03-05
**Status:** Approved

## Overview

Add email template management and email delivery verification to the existing worker pipeline. Uses Postmark for template-based sending and IMAP against `millionlabs.digital` (Private Email catch-all) for delivery verification. Integrates into the Deploy and E2E Testing phases — no new pipeline phase.

## Architecture Decisions

- **Approach:** Thin integration — shared utility module called by existing phases
- **Postmark isolation:** One Postmark Server per project (Account API key creates/manages Servers, each Server gets its own token)
- **Template source:** Architect identifies email touchpoints in `ARCHITECTURE.md`, builder creates template files, deployer pushes to Postmark
- **Email receiving:** `millionlabs.digital` catch-all inbox via Private Email (Namecheap)
- **Verification method:** IMAP for delivery confirmation + content parsing, Playwright for following verification links in the app
- **Addressing pattern:** `{job_id}-{role}@millionlabs.digital` — per-run, per-role isolation

## Module Structure

### New file: `worker/src/pipeline/email.py`

Two classes:

**PostmarkManager**
- `__init__(account_api_key: str)`
- `ensure_server(project_name: str) -> str` — Creates or retrieves Postmark Server by name. Returns server-level API token.
- `push_templates(server_token: str, templates: list[EmailTemplate]) -> None` — Upserts templates by alias (idempotent).
- `cleanup_server(project_name: str) -> None` — Optional teardown after job completes.

**InboxReader**
- `__init__(host: str, port: int, user: str, password: str)`
- `wait_for_email(recipient: str, subject_pattern: str, timeout: int = 60) -> ParsedEmail` — IMAP poll with exponential backoff (2s, 4s, 8s, 16s cap). Returns parsed email with `body_html`, `body_text`, `links[]`.
- `extract_verification_link(email: ParsedEmail, link_pattern: str) -> str` — Regex/parse to find action URL.
- `clear_inbox(recipient: str) -> None` — Delete emails for a recipient before test run.

### New dataclass in `worker/src/pipeline/models.py`

```python
@dataclass
class EmailTemplate:
    alias: str          # e.g., "welcome", "password-reset"
    name: str           # Human-readable name
    subject: str        # Subject line with {{variables}}
    html_body: str      # HTML template
    text_body: str      # Plain text fallback
```

### New config fields in `worker/src/config.py`

```
POSTMARK_ACCOUNT_API_KEY   # Account-level key (manages Servers)
PRIVATE_EMAIL_HOST         # mail.privateemail.com
PRIVATE_EMAIL_USER         # credentials for IMAP login
PRIVATE_EMAIL_PASSWORD     # stored as Fly secret
```

## Integration Points

### Deploy Phase (`deployer.py`)

After app deployment and health check:

1. Parse `ARCHITECTURE.md` for `## Email Templates` section
2. `PostmarkManager.ensure_server(project_name)` → server_token
3. Read template files from `emails/<alias>.html` and `emails/<alias>.txt`
4. `PostmarkManager.push_templates(server_token, templates)`
5. `flyctl secrets set POSTMARK_SERVER_TOKEN=<token> -a <app>`

If no `## Email Templates` section exists, skip entirely.

### E2E Testing Phase (`tester.py` / `e2e_loop.py`)

For E2E batches involving email flows:

1. `InboxReader.clear_inbox(f"{job_id}-{role}@millionlabs.digital")` — clean slate
2. E2E agent triggers the flow (e.g., signup with test email address)
3. `InboxReader.wait_for_email(recipient, subject_pattern, timeout=60)` — IMAP polls
4. `InboxReader.extract_verification_link(email, app_domain_pattern)` — extract URL
5. Playwright navigates to the extracted link — continues E2E flow

Steps 1/3/4 are deterministic Python in the orchestration layer, not agent decisions.

### Architect Prompt (`worker/src/prompts/planning.py`)

Expand architect system prompt to identify email touchpoints:

```
When designing the system, identify all email touchpoints:
- What triggers each email (user action or system event)
- Who receives it (user role)
- What the email contains (subject, key content, action link)
- Document in ARCHITECTURE.md under "## Email Templates":
  - alias: <template-alias>
  - subject: <subject with {{variables}}>
  - trigger: <what causes this email>
  - variables: <list of template variables>
```

### Builder Phase

When implementing email-related tasks:

1. Creates `emails/<alias>.html` and `emails/<alias>.txt` with Postmark `{{variable}}` syntax
2. Implements sending code using Postmark template API
3. Uses `{job_id}-{role}@millionlabs.digital` as test recipient in seed data

## Error Handling

- **IMAP timeout:** 60s with exponential backoff (2s, 4s, 8s, 16s cap). Failure → flow marked `FAILED` with reason `email_delivery_timeout`
- **Postmark idempotency:** `ensure_server` searches before creating. Template push upserts by alias. Safe to re-run on resume.
- **No-email projects:** No `## Email Templates` section → entire email flow skipped. Zero overhead.
- **Inbox cleanup:** `clear_inbox` scoped to `{job_id}-*` recipients before each E2E batch.
- **Resumability:** All Postmark operations are idempotent. IMAP is stateless per call.

## E2E Failure Reasons

New failure reasons for email-related E2E flows:

- `email_delivery_timeout` — email never arrived within timeout
- `email_link_invalid` — verification link extracted but returned error
- `email_content_mismatch` — email arrived but content didn't match expected pattern

These feed into the existing fix-retest loop with specific context for the fixer agent.

## Verification (`verifier.py`)

New method `verify_email_setup()`:

1. Check `emails/` directory exists with `.html` + `.txt` pairs
2. Verify each alias from `ARCHITECTURE.md` has matching template files
3. Check app code imports/uses Postmark SDK
4. Check template variables match between HTML and sending code

## Security

- All credentials via Fly secrets / env vars — never committed
- IMAP over SSL (port 993)
- Postmark Account API key (high privilege) only held by worker
- Deployed apps only receive Server-level tokens (scoped to their project)

## Config & Secrets Summary

| Secret | Scope | Purpose |
|--------|-------|---------|
| `POSTMARK_ACCOUNT_API_KEY` | Worker | Create/manage Postmark Servers |
| `POSTMARK_SERVER_TOKEN` | Deployed app | Send emails via templates |
| `PRIVATE_EMAIL_HOST` | Worker | IMAP server hostname |
| `PRIVATE_EMAIL_USER` | Worker | IMAP login username |
| `PRIVATE_EMAIL_PASSWORD` | Worker | IMAP login password |
