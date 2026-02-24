from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class PhaseProgress:
    status: str = "pending"  # pending | running | completed | failed | skipped
    started_at: str | None = None
    completed_at: str | None = None
    cost_usd: float = 0.0
    turns: int = 0
    evaluation_score: float | None = None
    retries: int = 0
    error: str | None = None


@dataclass
class PipelineProgress:
    job_id: str
    started_at: str
    tech_profile: dict = field(default_factory=dict)
    phases: dict[str, PhaseProgress] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    current_phase: str = ""


class ProgressTracker:
    """Reads and writes docs/PROGRESS.json in the repo to track pipeline state."""

    def __init__(self, repo_path: str, job_id: str) -> None:
        self.repo_path = Path(repo_path)
        self.progress_file = self.repo_path / "docs" / "PROGRESS.json"
        self.job_id = job_id

        if self.progress_file.exists():
            self.load()
        else:
            now = datetime.now(timezone.utc).isoformat()
            self.progress = PipelineProgress(job_id=job_id, started_at=now)
            self.save()

    def load(self) -> None:
        """Read PROGRESS.json from disk and populate self.progress."""
        data = json.loads(self.progress_file.read_text())
        phases = {}
        for name, phase_data in data.get("phases", {}).items():
            phases[name] = PhaseProgress(**phase_data)
        self.progress = PipelineProgress(
            job_id=data.get("job_id", self.job_id),
            started_at=data.get("started_at", ""),
            tech_profile=data.get("tech_profile", {}),
            phases=phases,
            total_cost_usd=data.get("total_cost_usd", 0.0),
            current_phase=data.get("current_phase", ""),
        )

    def save(self) -> None:
        """Write PROGRESS.json to disk."""
        self.progress_file.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self.progress)
        self.progress_file.write_text(json.dumps(data, indent=2) + "\n")

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _ensure_phase(self, phase: str) -> PhaseProgress:
        if phase not in self.progress.phases:
            self.progress.phases[phase] = PhaseProgress()
        return self.progress.phases[phase]

    def start_phase(self, phase: str) -> None:
        """Mark phase as running, set started_at to now."""
        p = self._ensure_phase(phase)
        p.status = "running"
        p.started_at = self._now()
        self.progress.current_phase = phase
        self.save()

    def complete_phase(self, phase: str) -> None:
        """Mark phase as completed, set completed_at."""
        p = self._ensure_phase(phase)
        p.status = "completed"
        p.completed_at = self._now()
        self.save()

    def fail_phase(self, phase: str, error: str) -> None:
        """Mark phase as failed with error message."""
        p = self._ensure_phase(phase)
        p.status = "failed"
        p.error = error
        p.completed_at = self._now()
        self.save()

    def skip_phase(self, phase: str) -> None:
        """Mark phase as skipped."""
        p = self._ensure_phase(phase)
        p.status = "skipped"
        self.save()

    def record_agent_result(self, phase: str, cost_usd: float, turns: int) -> None:
        """Add cost/turns to phase and update total_cost."""
        p = self._ensure_phase(phase)
        p.cost_usd += cost_usd
        p.turns += turns
        self.progress.total_cost_usd += cost_usd
        self.save()

    def record_evaluation(self, phase: str, score: float) -> None:
        """Set evaluation_score for a phase."""
        p = self._ensure_phase(phase)
        p.evaluation_score = score
        self.save()

    def is_phase_completed(self, phase: str) -> bool:
        """Check if a phase is completed."""
        p = self.progress.phases.get(phase)
        return p is not None and p.status == "completed"

    def is_task_completed(self, task_name: str) -> bool:
        """Check if a task phase is completed (checks phase f'task:{task_name}')."""
        return self.is_phase_completed(f"task:{task_name}")

    def update_tech_profile(self, profile: dict) -> None:
        """Set tech_profile."""
        self.progress.tech_profile = profile
        self.save()

    def get_skip_map(self) -> dict[str, bool]:
        """Return dict of phase_name: True for completed/skipped phases.

        Compatible with existing main.py skip logic.
        """
        skip = {}
        for name, phase in self.progress.phases.items():
            if phase.status in ("completed", "skipped"):
                skip[name] = True
        return skip
