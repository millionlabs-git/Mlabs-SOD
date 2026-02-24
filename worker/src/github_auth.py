"""GitHub App authentication — generate installation access tokens from a private key."""
from __future__ import annotations

import time

import httpx
import jwt


def generate_jwt(app_id: str, private_key: str) -> str:
    """Generate a short-lived JWT for GitHub App authentication."""
    now = int(time.time())
    payload = {
        "iat": now - 60,       # Issued at (60s in past for clock drift)
        "exp": now + (10 * 60),  # Expires in 10 minutes
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_installation_token(
    app_id: str,
    installation_id: str,
    private_key: str,
) -> str:
    """Exchange a GitHub App JWT for an installation access token.

    The returned token is valid for 1 hour and has the permissions
    configured on the GitHub App installation.
    """
    app_jwt = generate_jwt(app_id, private_key)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["token"]
