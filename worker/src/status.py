from __future__ import annotations

import httpx


class StatusReporter:
    """Fire-and-forget status updates to the orchestrator."""

    def __init__(self, orchestrator_url: str, job_id: str, webhook_secret: str):
        self.url = f"{orchestrator_url}/jobs/{job_id}/events"
        self.headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json",
        }

    async def report(self, event: str, detail: dict | None = None) -> None:
        """Post a status event. Logs errors but never raises."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.url,
                    json={"event": event, "detail": detail or {}},
                    headers=self.headers,
                    timeout=10,
                )
                resp.raise_for_status()
        except Exception as e:
            print(f"[status] Failed to report '{event}': {e}")
