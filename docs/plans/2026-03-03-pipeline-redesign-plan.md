# Pipeline Redesign Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the worker build pipeline: consolidate 11 subagents → 5, add Python verification gates, deploy checkpoints throughout, model hierarchy (Opus/Sonnet/Haiku), and user flow auditing.

**Architecture:** Keep the Claude Agent SDK orchestrator but add Python guardrails. The orchestrator (Opus) coordinates 5 subagents. Python scripts verify deliverables between phases. Three deploy checkpoints to the same Fly app catch deployment issues early. LLM-as-Judge (Haiku) validates acceptance criteria per task.

**Tech Stack:** Python 3.12, claude-agent-sdk, Fly.io (flyctl), Neon (MCP), Playwright

---

## Task 1: Create `verifier.py` — the Python verification module

**Files:**
- Create: `worker/src/orchestrator/verifier.py`
- Test: `worker/tests/test_verifier.py`

**Step 1: Write failing tests for StructuralChecker**

```python
# worker/tests/test_verifier.py
import pytest
from pathlib import Path
from unittest.mock import patch
from src.orchestrator.verifier import StructuralChecker, VerifyResult


class TestStructuralChecker:
    def setup_method(self):
        self.checker = StructuralChecker("/fake/repo")

    def test_check_no_stubs_finds_todos(self, tmp_path):
        """Grep for TODO/FIXME/placeholder in source files."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.ts").write_text("// TODO: implement this\nconst x = 1;")
        checker = StructuralChecker(str(tmp_path))
        issues = checker.check_no_stubs()
        assert len(issues) >= 1
        assert "TODO" in issues[0]

    def test_check_no_stubs_clean(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.ts").write_text("const x = 1;\nexport default x;")
        checker = StructuralChecker(str(tmp_path))
        issues = checker.check_no_stubs()
        assert issues == []

    def test_check_mock_patterns_finds_anonymous_auth(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "auth.ts").write_text("signInAnonymously(auth);")
        checker = StructuralChecker(str(tmp_path))
        issues = checker.check_mock_patterns()
        assert len(issues) >= 1
        assert "signInAnonymously" in issues[0]

    def test_check_mock_patterns_clean(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "auth.ts").write_text("signInWithEmailAndPassword(auth, email, pw);")
        checker = StructuralChecker(str(tmp_path))
        issues = checker.check_mock_patterns()
        assert issues == []
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_verifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.orchestrator.verifier'`

**Step 3: Write VerifyResult and StructuralChecker**

```python
# worker/src/orchestrator/verifier.py
"""Verification gates for pipeline phases.

Each verify_* method returns a VerifyResult with pass/fail and specific issues.
StructuralChecker does file-system checks (grep, file existence) without LLM calls.
PhaseVerifier orchestrates checks per phase and runs shell commands.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VerifyResult:
    """Result of a verification check."""
    passed: bool
    issues: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.passed


STUB_PATTERNS = [
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\bplaceholder\b",
    r"\bXXX\b",
]

MOCK_PATTERNS = [
    r"signInAnonymously",
    r"mock-auth",
    r"fake-token",
    r"hardcoded-user",
    r"skip-auth",
    r"\bmock[A-Z]\w*\(",  # mockFunction( style calls in source (not test) files
]

# Directories to skip during source file scanning
SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "__pycache__",
    ".venv", "venv", ".claude", ".cache", "coverage", ".turbo",
}


class StructuralChecker:
    """File-system checks: stubs, mocks, route wiring, model wiring."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = Path(repo_path)

    def _source_files(self) -> list[Path]:
        """List source files, excluding node_modules, tests, dist, etc."""
        files: list[Path] = []
        for path in self.repo_path.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(self.repo_path).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            # Skip test files for mock pattern checks
            if path.suffix not in (".ts", ".tsx", ".js", ".jsx", ".py", ".go"):
                continue
            files.append(path)
        return files

    def _test_files(self) -> list[Path]:
        """List test files only."""
        files: list[Path] = []
        for path in self.repo_path.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(self.repo_path).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            name = path.name.lower()
            if ".test." in name or ".spec." in name or name.startswith("test_"):
                files.append(path)
        return files

    def check_no_stubs(self) -> list[str]:
        """Find TODO/FIXME/placeholder patterns in source files."""
        issues: list[str] = []
        for path in self._source_files():
            try:
                content = path.read_text(errors="replace")
            except OSError:
                continue
            rel = path.relative_to(self.repo_path)
            # Skip test files — TODOs in tests are less critical
            name = path.name.lower()
            if ".test." in name or ".spec." in name or name.startswith("test_"):
                continue
            for i, line in enumerate(content.splitlines(), 1):
                for pattern in STUB_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        issues.append(f"{rel}:{i}: {line.strip()}")
        return issues

    def check_mock_patterns(self) -> list[str]:
        """Find mock/anonymous/hardcoded patterns in non-test source files."""
        issues: list[str] = []
        for path in self._source_files():
            name = path.name.lower()
            # Only check non-test source files
            if ".test." in name or ".spec." in name or name.startswith("test_"):
                continue
            try:
                content = path.read_text(errors="replace")
            except OSError:
                continue
            rel = path.relative_to(self.repo_path)
            for i, line in enumerate(content.splitlines(), 1):
                for pattern in MOCK_PATTERNS:
                    if re.search(pattern, line):
                        issues.append(f"{rel}:{i}: {line.strip()}")
        return issues

    def check_route_wiring(self, expected_routes: list[str]) -> list[str]:
        """Check that expected API routes exist in source files.

        Args:
            expected_routes: list of route paths like ["/api/users", "/api/posts"]

        Returns list of missing routes.
        """
        # Read all source files into a single blob for searching
        all_source = ""
        for path in self._source_files():
            try:
                all_source += path.read_text(errors="replace") + "\n"
            except OSError:
                continue

        missing: list[str] = []
        for route in expected_routes:
            # Check for the route path in source (handles various patterns)
            # e.g. "/api/users", '/api/users', `api/users`, router.get("/api/users"
            route_stripped = route.lstrip("/")
            if route not in all_source and route_stripped not in all_source:
                missing.append(f"Route not found in source: {route}")
        return missing

    def check_model_wiring(self, expected_models: list[str]) -> list[str]:
        """Check that expected data models exist in source files.

        Args:
            expected_models: list of model names like ["User", "Post"]

        Returns list of missing models.
        """
        all_source = ""
        for path in self._source_files():
            try:
                all_source += path.read_text(errors="replace") + "\n"
            except OSError:
                continue

        missing: list[str] = []
        for model in expected_models:
            # Look for class/interface/table/schema definitions
            patterns = [
                rf"\b{model}\b",  # basic name reference
            ]
            found = any(re.search(p, all_source) for p in patterns)
            if not found:
                missing.append(f"Model not found in source: {model}")
        return missing

    def check_test_has_assertions(self, test_file: str) -> list[str]:
        """Check that a test file contains real assertions."""
        path = self.repo_path / test_file
        if not path.exists():
            return [f"Test file does not exist: {test_file}"]
        content = path.read_text(errors="replace")
        assertion_patterns = [
            r"\bexpect\b", r"\bassert\b", r"\btoBe\b", r"\btoEqual\b",
            r"\btoHaveBeenCalled\b", r"\btoThrow\b", r"\btoContain\b",
            r"\btoMatch\b", r"\brejects\b", r"\bresolves\b",
        ]
        for pattern in assertion_patterns:
            if re.search(pattern, content):
                return []
        return [f"No assertions found in {test_file}"]


class PhaseVerifier:
    """Orchestrates per-phase verification using StructuralChecker + shell commands."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path
        self.checker = StructuralChecker(repo_path)

    def _run_cmd(self, cmd: list[str], timeout: int = 180) -> tuple[bool, str]:
        """Run a shell command, return (success, output)."""
        try:
            result = subprocess.run(
                cmd, cwd=self.repo_path,
                capture_output=True, text=True, timeout=timeout,
            )
            output = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
        except FileNotFoundError:
            return False, f"Command not found: {cmd[0]}"

    def verify_architecture(self) -> VerifyResult:
        """Verify architect deliverables: ARCHITECTURE.md + BUILD_PLAN.md."""
        issues: list[str] = []
        repo = Path(self.repo_path)

        # Check files exist and are non-empty
        for filename in ["docs/ARCHITECTURE.md", "docs/BUILD_PLAN.md"]:
            path = repo / filename
            if not path.exists():
                issues.append(f"Missing: {filename}")
                continue
            content = path.read_text()
            if len(content.strip()) < 100:
                issues.append(f"{filename} is too short ({len(content)} chars)")

        # Check ARCHITECTURE.md has required sections
        arch_path = repo / "docs/ARCHITECTURE.md"
        if arch_path.exists():
            content = arch_path.read_text().lower()
            for section in ["data model", "api", "endpoint", "route"]:
                if section not in content:
                    issues.append(f"ARCHITECTURE.md missing section about: {section}")

        # Check BUILD_PLAN.md has tasks with acceptance criteria
        plan_path = repo / "docs/BUILD_PLAN.md"
        if plan_path.exists():
            content = plan_path.read_text()
            if "acceptance criteria" not in content.lower():
                issues.append("BUILD_PLAN.md tasks missing acceptance criteria")
            # Count tasks
            task_count = len(re.findall(r"##\s+Task\s+\d+", content))
            if task_count < 3:
                issues.append(f"BUILD_PLAN.md has only {task_count} tasks (expected 8-15)")

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    def verify_scaffold(self) -> VerifyResult:
        """Verify scaffold deliverables: builds, tests pass, structural wiring."""
        issues: list[str] = []

        # npm install
        ok, output = self._run_cmd(["npm", "install"])
        if not ok:
            issues.append(f"npm install failed: {output[:300]}")
            return VerifyResult(passed=False, issues=issues)

        # npm run build
        ok, output = self._run_cmd(["npm", "run", "build"])
        if not ok:
            issues.append(f"npm run build failed: {output[:300]}")

        # npm test
        ok, output = self._run_cmd(["npm", "test"])
        if not ok:
            issues.append(f"npm test failed: {output[:300]}")

        # Structural: no stubs
        stub_issues = self.checker.check_no_stubs()
        if stub_issues:
            issues.append(f"Found {len(stub_issues)} TODO/FIXME/placeholder stubs")
            issues.extend(stub_issues[:5])  # Show first 5

        # Structural: no mock patterns in source
        mock_issues = self.checker.check_mock_patterns()
        if mock_issues:
            issues.append(f"Found {len(mock_issues)} mock/placeholder patterns in source")
            issues.extend(mock_issues[:5])

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    def verify_task(self, task_name: str, new_test_files: list[str] | None = None) -> VerifyResult:
        """Verify a builder task: builds, tests pass, test file exists with assertions."""
        issues: list[str] = []

        # npm run build
        ok, output = self._run_cmd(["npm", "run", "build"])
        if not ok:
            issues.append(f"npm run build failed: {output[:300]}")

        # npm test
        ok, output = self._run_cmd(["npm", "test"])
        if not ok:
            issues.append(f"npm test failed: {output[:300]}")

        # Check test files have assertions
        if new_test_files:
            for tf in new_test_files:
                assertion_issues = self.checker.check_test_has_assertions(tf)
                issues.extend(assertion_issues)

        # Check no new stubs or mocks introduced
        stub_issues = self.checker.check_no_stubs()
        if stub_issues:
            issues.append(f"Found {len(stub_issues)} TODO/FIXME stubs in source")
            issues.extend(stub_issues[:3])

        mock_issues = self.checker.check_mock_patterns()
        if mock_issues:
            issues.append(f"Found {len(mock_issues)} mock patterns in non-test source")
            issues.extend(mock_issues[:3])

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    def verify_review(self) -> VerifyResult:
        """Verify review deliverables: docs exist, no unfixed criticals, e2e passed."""
        issues: list[str] = []
        repo = Path(self.repo_path)

        for filename in ["docs/CODE_REVIEW.md", "docs/PR_DESCRIPTION.md"]:
            path = repo / filename
            if not path.exists():
                issues.append(f"Missing: {filename}")
            elif len(path.read_text().strip()) < 50:
                issues.append(f"{filename} is too short")

        # Check for unfixed critical issues
        review_path = repo / "docs/CODE_REVIEW.md"
        if review_path.exists():
            content = review_path.read_text()
            # Look for CRITICAL/HIGH that aren't marked FIXED
            for line in content.splitlines():
                line_upper = line.upper()
                if ("CRITICAL" in line_upper or "HIGH" in line_upper) and "FIXED" not in line_upper:
                    issues.append(f"Unfixed critical/high issue: {line.strip()[:100]}")

        # Check screenshots exist
        screenshots_dir = repo / "docs" / "screenshots" / "e2e"
        if not screenshots_dir.exists() or not list(screenshots_dir.glob("*.png")):
            issues.append("No e2e screenshots found in docs/screenshots/e2e/")

        # Final build + test
        ok, output = self._run_cmd(["npm", "run", "build"])
        if not ok:
            issues.append(f"Final build failed: {output[:300]}")
        ok, output = self._run_cmd(["npm", "test"])
        if not ok:
            issues.append(f"Final tests failed: {output[:300]}")

        return VerifyResult(passed=len(issues) == 0, issues=issues)
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_verifier.py -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add worker/src/orchestrator/verifier.py worker/tests/test_verifier.py
git commit -m "feat: add verifier module with structural checks and phase verification"
```

---

## Task 2: Add `deploy_checkpoint()` to deployer

**Files:**
- Modify: `worker/src/pipeline/deployer.py:1-53` (add function after `_neon_mcp`)

**Step 1: Write failing test**

```python
# worker/tests/test_deploy_checkpoint.py
import pytest
from unittest.mock import patch, MagicMock
from src.pipeline.deployer import deploy_checkpoint


class TestDeployCheckpoint:
    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_success(self, mock_run):
        """Health check returns 200 → pass."""
        # flyctl deploy succeeds
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="deployed", stderr=""),  # flyctl deploy
            MagicMock(returncode=0, stdout="200", stderr=""),  # curl health check
        ]
        result = deploy_checkpoint("/fake/repo", "test-app", "smoke-test")
        assert result.passed is True

    @patch("src.pipeline.deployer.subprocess.run")
    def test_deploy_checkpoint_health_fail(self, mock_run):
        """Health check returns non-200 → fail."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="deployed", stderr=""),  # flyctl deploy
            MagicMock(returncode=0, stdout="502", stderr=""),  # curl health check
        ]
        result = deploy_checkpoint("/fake/repo", "test-app", "smoke-test")
        assert result.passed is False
        assert any("health" in i.lower() for i in result.issues)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_deploy_checkpoint.py -v`
Expected: FAIL with `ImportError: cannot import name 'deploy_checkpoint'`

**Step 3: Add `deploy_checkpoint` function to `deployer.py`**

Add this function after the `_neon_mcp` function (after line 52 in `worker/src/pipeline/deployer.py`):

```python
@dataclass
class DeployCheckpointResult:
    """Result of a deploy checkpoint."""
    passed: bool
    issues: list[str] = field(default_factory=list)
    logs: str = ""


def deploy_checkpoint(
    repo_path: str,
    app_name: str,
    checkpoint_name: str,
    health_path: str = "/",
    timeout: int = 180,
) -> DeployCheckpointResult:
    """Lightweight redeploy to an existing Fly app and verify health.

    Does NOT create the app or set secrets — assumes the app already exists.
    Used for Checkpoints 2 and 3 (Checkpoint 1 uses the full deploy flow).

    Args:
        repo_path: Path to the project repo.
        app_name: Existing Fly app name (e.g., "sod-abc12345").
        checkpoint_name: Label for logging (e.g., "midpoint", "pre-review").
        health_path: Path to hit for health check (default: "/").
        timeout: Deploy timeout in seconds.
    """
    issues: list[str] = []
    logs = ""

    print(f"[deploy-checkpoint:{checkpoint_name}] Deploying to {app_name}...")

    # flyctl deploy (reuses existing app + machine)
    try:
        result = subprocess.run(
            ["flyctl", "deploy", "--app", app_name, "--yes"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        logs = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode != 0:
            issues.append(f"flyctl deploy failed (exit {result.returncode})")
            return DeployCheckpointResult(passed=False, issues=issues, logs=logs)
    except subprocess.TimeoutExpired:
        issues.append(f"Deploy timed out after {timeout}s")
        return DeployCheckpointResult(passed=False, issues=issues, logs="timeout")

    # Health check
    url = f"https://{app_name}.fly.dev{health_path}"
    print(f"[deploy-checkpoint:{checkpoint_name}] Health check: {url}")

    try:
        check = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
            capture_output=True, text=True, timeout=15,
        )
        status = check.stdout.strip()
        if status in ("200", "201", "301", "302"):
            print(f"[deploy-checkpoint:{checkpoint_name}] Health check passed ({status})")
        else:
            issues.append(f"Health check failed: {url} returned {status}")
    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        issues.append(f"Health check error: {e}")

    return DeployCheckpointResult(
        passed=len(issues) == 0, issues=issues, logs=logs
    )
```

Also add the import at the top of `deployer.py`:
```python
from dataclasses import dataclass, field
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_deploy_checkpoint.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add worker/src/pipeline/deployer.py worker/tests/test_deploy_checkpoint.py
git commit -m "feat: add deploy_checkpoint for lightweight redeploy verification"
```

---

## Task 3: Update `progress.py` to track deploy state

**Files:**
- Modify: `worker/src/orchestrator/progress.py:21-28` (add deploy fields to PipelineProgress)

**Step 1: Write failing test**

```python
# worker/tests/test_progress_deploy.py
import json
import pytest
from src.orchestrator.progress import ProgressTracker


class TestProgressDeployTracking:
    def test_set_deploy_info(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        tracker = ProgressTracker(str(tmp_path), "test-job")
        tracker.set_deploy_info("sod-abc123", "https://sod-abc123.fly.dev")
        tracker.save()

        data = json.loads((docs / "PROGRESS.json").read_text())
        assert data["deploy_app_name"] == "sod-abc123"
        assert data["deploy_url"] == "https://sod-abc123.fly.dev"

    def test_get_deploy_info(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        tracker = ProgressTracker(str(tmp_path), "test-job")
        tracker.set_deploy_info("sod-abc123", "https://sod-abc123.fly.dev")
        tracker.save()

        # Reload
        tracker2 = ProgressTracker(str(tmp_path), "test-job")
        assert tracker2.progress.deploy_app_name == "sod-abc123"
        assert tracker2.progress.deploy_url == "https://sod-abc123.fly.dev"
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_progress_deploy.py -v`
Expected: FAIL with `AttributeError: 'ProgressTracker' has no attribute 'set_deploy_info'`

**Step 3: Add deploy fields to `PipelineProgress` and helper to `ProgressTracker`**

In `worker/src/orchestrator/progress.py`, add to the `PipelineProgress` dataclass (line 28):

```python
@dataclass
class PipelineProgress:
    job_id: str
    started_at: str
    tech_profile: dict = field(default_factory=dict)
    phases: dict[str, PhaseProgress] = field(default_factory=dict)
    total_cost_usd: float = 0.0
    current_phase: str = ""
    deploy_app_name: str = ""
    deploy_url: str = ""
```

Add to `ProgressTracker` class (after `update_tech_profile`):

```python
    def set_deploy_info(self, app_name: str, deploy_url: str) -> None:
        """Store Fly app name and URL for deploy checkpoints."""
        self.progress.deploy_app_name = app_name
        self.progress.deploy_url = deploy_url
        self.save()
```

Update the `load()` method to read the new fields (add after line 58):

```python
        self.progress = PipelineProgress(
            job_id=data.get("job_id", self.job_id),
            started_at=data.get("started_at", ""),
            tech_profile=data.get("tech_profile", {}),
            phases=phases,
            total_cost_usd=data.get("total_cost_usd", 0.0),
            current_phase=data.get("current_phase", ""),
            deploy_app_name=data.get("deploy_app_name", ""),
            deploy_url=data.get("deploy_url", ""),
        )
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_progress_deploy.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add worker/src/orchestrator/progress.py worker/tests/test_progress_deploy.py
git commit -m "feat: track deploy app name and URL in PROGRESS.json"
```

---

## Task 4: Update `component_loader.py` — consolidate loader methods

**Files:**
- Modify: `worker/src/orchestrator/component_loader.py:89-157`

The consolidated reviewer agent needs skills from code-reviewer + security-reviewer + database-reviewer + e2e-runner. The planner method is no longer needed (absorbed into architect).

**Step 1: Write failing test**

```python
# worker/tests/test_component_loader.py
import pytest
from unittest.mock import MagicMock
from src.orchestrator.component_loader import ComponentLoader


class TestConsolidatedLoader:
    def test_for_reviewer_loads_all_review_skills(self):
        """Consolidated reviewer should load code, security, DB, and e2e skills."""
        tp = MagicMock()
        tp.rules = ["typescript/coding-style"]
        tp.skills = []
        loader = ComponentLoader("/fake/config", "/fake/vp/SKILL.md", tp)
        # Patch _load_agent and _load_skill to track what's loaded
        loaded = []
        original_load_agent = loader._load_agent
        original_load_skill = loader._load_skill
        loader._load_agent = lambda name: (loaded.append(f"agent:{name}"), "")[1]
        loader._load_skill = lambda name: (loaded.append(f"skill:{name}"), "")[1]
        loader._load_rule = lambda name: ""

        loader.for_reviewer()

        assert "agent:code-reviewer.md" in loaded
        assert "skill:security-review" in loaded or "skill:security-scan" in loaded
        assert "skill:e2e-testing" in loaded
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_component_loader.py::TestConsolidatedLoader -v`
Expected: FAIL (current `for_reviewer` only loads code-reviewer agent + security-review skill)

**Step 3: Update `for_reviewer` and remove `for_planner`**

In `worker/src/orchestrator/component_loader.py`, replace the `for_reviewer` method and remove methods for the absorbed agents:

```python
    def for_reviewer(self) -> str:
        """Build system prompt for the consolidated reviewer phase.

        Loads code review, security review, DB review, and e2e testing skills
        into a single reviewer agent.
        """
        return self._combine([
            self._load_agent("code-reviewer.md"),
            self._load_skill("security-review"),
            self._load_skill("security-scan"),
            self._load_skill("e2e-testing"),
            self._load_skill("visual-playwright"),
            self._load_skill("postgres-patterns"),
            self._load_skill("database-migrations"),
            self._load_tech_rules(),
        ])

    def for_architect(self) -> str:
        """Build system prompt for the architect phase (now includes planning)."""
        return self._combine([
            self._load_agent("architect.md"),
            self._load_agent("planner.md"),
            self._load_skill("api-design"),
            self._load_tech_rules(),
        ])
```

Remove `for_planner`, `for_security_reviewer`, `for_db_reviewer`, `for_e2e_runner` methods — they're no longer called.

**Step 4: Run test to verify it passes**

Run: `cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/test_component_loader.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add worker/src/orchestrator/component_loader.py worker/tests/test_component_loader.py
git commit -m "refactor: consolidate component loader for 5-agent architecture"
```

---

## Task 5: Rewrite `_build_subagents()` — 11 → 5 agents

**Files:**
- Modify: `worker/src/orchestrator/runner.py:28-396`

This is the biggest change. Replace the entire `_build_subagents` function body.

**Step 1: No separate test file needed — we verify via syntax check + integration**

Run: `python3 -c "import ast; ast.parse(open('worker/src/orchestrator/runner.py').read())"` after the change.

**Step 2: Replace `_build_subagents` function**

Replace lines 28-396 of `worker/src/orchestrator/runner.py` with:

```python
def _build_subagents(
    loader: ComponentLoader,
    context_builder: ContextBuilder,
    config: Config,
    tech_profile: TechProfile,
    repo_path: str,
    has_db: bool,
) -> dict[str, AgentDefinition]:
    """Build the AgentDefinition map — 5 consolidated subagents."""

    vp_script = config.vp_script_path
    screenshots_base = f"{repo_path}/docs/screenshots"

    agents: dict[str, AgentDefinition] = {}

    # ── Architect (absorbs planner) ───────────────────────────────────
    architect_system = loader.for_architect()
    agents["architect"] = AgentDefinition(
        description=(
            "System architect and planner. Use this agent to design the "
            "technical architecture AND decompose it into ordered build tasks. "
            "It writes docs/ARCHITECTURE.md and docs/BUILD_PLAN.md."
        ),
        prompt=(
            f"{architect_system}\n\n" if architect_system else ""
        ) + (
            "You are an expert software architect. Read the PRD provided "
            "and produce TWO documents:\n\n"
            "## Document 1: docs/ARCHITECTURE.md\n"
            "Decide on:\n"
            "- Tech stack (languages, frameworks, databases)\n"
            "- Directory structure\n"
            "- Major components and responsibilities\n"
            "- Data models and schemas (ALL tables, ALL columns, ALL relations)\n"
            "- API contracts (every endpoint, method, request/response shapes)\n"
            "- Frontend routes and pages (every page, its purpose, key components)\n"
            "- Authentication and authorization approach\n"
            "- Error handling strategy\n\n"
            "## Document 2: docs/BUILD_PLAN.md\n"
            "Break the implementation into 8-15 ordered tasks.\n\n"
            "Each task should be a complete vertical slice — not just "
            "'create file X'. It should produce working, connected code.\n\n"
            "Order: database layer → API routes → frontend pages → integration.\n\n"
            "Use this exact format for each task:\n\n"
            "## Task N: <name>\n"
            "- **Description:** 2-4 sentences with specifics\n"
            "- **Files:** files to create/modify\n"
            "- **Dependencies:** task numbers or None\n"
            "- **Has UI:** true/false\n"
            "- **Route:** /path (if UI)\n"
            "- **Acceptance Criteria:**\n"
            "  - Specific, testable criterion\n\n"
            "Write ARCHITECTURE.md first, then BUILD_PLAN.md."
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="opus",
    )

    # ── Scaffolder ────────────────────────────────────────────────────
    scaffolder_system = loader.for_scaffolder()
    scaffolder_context = context_builder.for_scaffolder()
    agents["scaffolder"] = AgentDefinition(
        description=(
            "Project scaffolder. Use this agent to create the full project "
            "skeleton — directory structure, package.json with ALL deps, "
            "configs, database schema, route stubs, shared types, test infra. "
            "It must build clean with zero errors and npm test must pass."
        ),
        prompt=(
            f"{scaffolder_system}\n\n" if scaffolder_system else ""
        ) + (
            f"## Project Context\n\n{scaffolder_context}\n\n---\n\n"
            if scaffolder_context else ""
        ) + (
            "Create the full project scaffold based on the architecture and "
            "build plan above (also available in docs/ARCHITECTURE.md and "
            "docs/BUILD_PLAN.md).\n\n"
            "1. Directory structure matching the architecture exactly\n"
            "2. Package manager config with ALL dependencies for the full build\n"
            "3. Language configs (tsconfig.json, .eslintrc, etc.)\n"
            "4. Database schema/ORM models — all tables, all columns, all relations\n"
            "5. API route stubs with correct paths, methods, parameter types\n"
            "6. Shared types/interfaces\n"
            "7. Test infrastructure\n"
            "8. E2E test infrastructure:\n"
            "   - Install vitest (or jest) + supertest for API integration testing\n"
            "   - Create a test helper that starts the app server and provides a supertest instance\n"
            "   - Create a test DB setup/teardown helper (or in-memory SQLite fallback)\n"
            "   - Add a working example integration test that hits a health/root endpoint\n"
            "   - Ensure `npm test` works and passes from the start\n"
            "   - Install @playwright/test for browser e2e (configured but no tests yet)\n"
            "9. .env.example with all required vars\n\n"
            "Rules:\n"
            "- Install ALL deps and verify `npm run build` (or equivalent) succeeds\n"
            "- `npm test` MUST pass before scaffold is complete — run it and paste the output\n"
            "- No TODO comments — use minimal valid implementations instead\n"
            "- Define real data models with all fields, not just id and name"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Builder (feature implementer) ─────────────────────────────────
    builder_system = loader.for_builder()
    builder_context = context_builder.for_builder(
        task_name="", task_files=[], completed_tasks=[]
    )
    agents["builder"] = AgentDefinition(
        description=(
            "Feature builder. Use this agent to implement a specific task "
            "from the build plan. Give it the task number, name, description, "
            "and acceptance criteria. It writes complete, working code — no "
            "TODOs, no stubs, fully wired up."
        ),
        prompt=(
            f"{builder_system}\n\n" if builder_system else ""
        ) + (
            f"## Project Context\n\n{builder_context}\n\n---\n\n"
            if builder_context else ""
        ) + (
            "You are a feature implementer who works with TEST-DRIVEN DEVELOPMENT.\n\n"
            "## TDD Cycle (mandatory for every task)\n\n"
            "For each feature in the task:\n"
            "1. **RED** — Write a failing API integration test FIRST:\n"
            "   - Use supertest to hit the real endpoint with real DB\n"
            "   - Test the actual behavior: POST creates, GET retrieves, etc.\n"
            "   - Run the test. Watch it FAIL. Confirm it fails for the RIGHT reason.\n"
            "   - If the test passes immediately, it proves nothing — delete it and write a real one.\n\n"
            "2. **GREEN** — Write the MINIMUM code to make the test pass:\n"
            "   - Implement the route, controller, DB query — whatever the test needs\n"
            "   - Wire everything together: route → handler → DB → response\n"
            "   - Run the test again. It MUST pass now.\n\n"
            "3. **REFACTOR** — Clean up while tests stay green:\n"
            "   - Remove duplication, improve naming\n"
            "   - Run tests after each change to confirm nothing broke\n\n"
            "## Verification Gate (before claiming done)\n\n"
            "You MUST run these commands and paste the ACTUAL output:\n"
            "```\n"
            "npm run build    # Must exit 0\n"
            "npm test         # Must show 0 failures\n"
            "```\n"
            "If you haven't pasted real output from these commands, you are NOT done.\n"
            "NO 'should work', 'looks correct', 'probably passes' — EVIDENCE ONLY.\n\n"
            "## Testing Rules\n\n"
            "- Test REAL behavior, not mocks. Hit real endpoints, query real DB.\n"
            "- Every API route must have an integration test.\n"
            "- Every DB write must be verified with a read-back in the test.\n"
            "- Do NOT mock the database or HTTP layer in integration tests.\n"
            "- Tests written AFTER code pass immediately — that proves nothing.\n\n"
            "## Implementation Rules\n\n"
            "- No placeholder returns, no // TODO, no pass stubs\n"
            "- Wire everything: API → DB, UI → API, imports → usage\n"
            "- Use dependencies already in package.json\n"
            "- Search for TODO/FIXME/placeholder and replace with real code"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Fixer (absorbs build-error-resolver) ──────────────────────────
    fixer_system = loader.for_build_error_resolver()
    agents["fixer"] = AgentDefinition(
        description=(
            "Fixer. Use this agent when builds fail, tests fail, deploys fail, "
            "or review finds critical issues. Give it the error output or issue "
            "description and it will diagnose and fix systematically."
        ),
        prompt=(
            f"{fixer_system}\n\n" if fixer_system else ""
        ) + (
            "You fix build, test, deploy, and review failures using SYSTEMATIC DEBUGGING.\n\n"
            "## Phase 1: Investigate (before ANY fix)\n"
            "- Read the FULL error message carefully\n"
            "- Reproduce: run the failing command yourself\n"
            "- Gather evidence: which file, which line, what was expected vs actual\n"
            "- Check recent changes: what was the last thing modified?\n\n"
            "## Phase 2: Analyze\n"
            "- Find a WORKING example of similar code in the project\n"
            "- Compare the working code with the broken code line by line\n"
            "- Identify the specific difference causing the failure\n\n"
            "## Phase 3: Fix (one change at a time)\n"
            "- Form ONE hypothesis about the root cause\n"
            "- Make ONE targeted change\n"
            "- Run the failing command again to verify\n"
            "- If it still fails, REVERT and try a different hypothesis\n\n"
            "## Phase 4: Escalate\n"
            "- After 3 failed fix attempts, STOP patching\n"
            "- The problem is likely architectural, not a typo\n"
            "- Read the architecture doc and reconsider the approach\n"
            "- Report what you've tried and what you think the real issue is\n\n"
            "## Verification\n"
            "After fixing, run BOTH:\n"
            "```\n"
            "npm run build    # Must exit 0\n"
            "npm test         # Must show 0 failures\n"
            "```\n"
            "Paste the actual output. No assumptions."
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="sonnet",
    )

    # ── Reviewer (absorbs code/security/db reviewers + e2e + pr-writer) ─
    reviewer_system = loader.for_reviewer()
    reviewer_context = context_builder.for_reviewer()
    db_instructions = ""
    if has_db:
        db_instructions = (
            "\n## Database Review\n"
            "- Schema design (normalization, indexes, constraints)\n"
            "- Query patterns (N+1 problems, missing indexes)\n"
            "- Migration safety\n"
            "- Connection pooling and error handling\n"
        )
    agents["reviewer"] = AgentDefinition(
        description=(
            "Reviewer. Use this agent for the full review phase: code quality, "
            "security audit, database review, Playwright e2e browser tests, and "
            "PR description generation. One agent, one pass over the codebase."
        ),
        prompt=(
            f"{reviewer_system}\n\n" if reviewer_system else ""
        ) + (
            f"## Project Context\n\n{reviewer_context}\n\n---\n\n"
            if reviewer_context else ""
        ) + (
            "You are a comprehensive reviewer. Perform ALL of the following "
            "in a single pass:\n\n"
            "## 1. Code Quality Review\n"
            "- Code quality and maintainability\n"
            "- Error handling completeness\n"
            "- Test coverage gaps\n"
            "- Performance concerns\n"
            "- Unused code, dead imports\n\n"
            "## 2. Security Audit\n"
            "- Hardcoded secrets or credentials\n"
            "- Injection vulnerabilities (SQL, command, XSS)\n"
            "- Authentication and authorization issues\n"
            "- Dependency vulnerabilities (run npm audit)\n"
            "- Insecure configurations\n"
            "- Missing input validation\n"
            f"{db_instructions}\n"
            f"## 3. E2E Browser Tests\n"
            f"Start the dev server and use Visual Playwright to test every page:\n"
            f"1. Read docs/ARCHITECTURE.md for all routes and user flows\n"
            f"2. Start the dev server\n"
            f"3. Visit every page:\n"
            f"   node {vp_script} goto \"http://localhost:3000\" "
            f"--screenshot {screenshots_base}/e2e/home.png\n"
            f"4. Test key user flows: signup, login, CRUD operations, forms\n"
            f"5. Take screenshots of each page\n"
            f"6. Close sessions: node {vp_script} close\n\n"
            "## 4. Write Deliverables\n"
            "- Write docs/CODE_REVIEW.md with all findings (file:line references)\n"
            "  Mark severity: CRITICAL, HIGH, MEDIUM, LOW\n"
            "  Mark fixed items as FIXED\n"
            "- Write docs/PR_DESCRIPTION.md:\n"
            "  1. Summary of what was built (1-2 paragraphs)\n"
            "  2. PRD requirements mapped to implementing code\n"
            "  3. Architectural decisions\n"
            "  4. Deferred items or limitations\n"
            "  5. Test coverage stats\n"
            "  6. Reference screenshots from docs/screenshots/\n\n"
            "Fix critical issues directly. Document minor issues in the review.\n"
            f"Keep screenshots in {screenshots_base}/e2e/"
        ),
        tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        model="opus",
    )

    return agents
```

**Step 3: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('worker/src/orchestrator/runner.py').read()); print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add worker/src/orchestrator/runner.py
git commit -m "refactor: consolidate 11 subagents to 5 (architect, scaffolder, builder, fixer, reviewer)"
```

---

## Task 6: Rewrite `_build_orchestrator_prompt()` — new pipeline phases

**Files:**
- Modify: `worker/src/orchestrator/runner.py` (the `_build_orchestrator_prompt` function)

**Step 1: Replace the entire orchestrator prompt function**

Replace `_build_orchestrator_prompt` with the new phases including deploy checkpoints, verification gates, user flow audit, and model hierarchy:

```python
def _build_orchestrator_prompt(
    prd_content: str,
    repo_path: str,
    config: Config,
    branch_name: str,
    skip: dict[str, bool],
    has_db: bool,
) -> str:
    """Build the main prompt for the orchestrator agent."""

    skip_instructions = ""
    if skip:
        skipped = [phase for phase, done in skip.items() if done]
        if skipped:
            skip_instructions = (
                f"\n**IMPORTANT: Skip these already-completed phases: "
                f"{', '.join(skipped)}**\n"
            )

    deploy_enabled = bool(config.fly_api_token)
    deploy_checkpoint_instructions = ""
    if deploy_enabled:
        deploy_checkpoint_instructions = (
            "\n### Deploy Checkpoint 1: Smoke Test (after scaffold)\n"
            f"1. Create Fly app and provision infrastructure\n"
            f"2. Run `flyctl deploy` and verify health endpoint returns 200\n"
            f"3. If fails, send deploy logs to **fixer**, redeploy (max 3 attempts)\n"
            f"4. Save the app name and URL — you will redeploy to the SAME app later\n\n"
        )

    deploy_midpoint = ""
    if deploy_enabled:
        deploy_midpoint = (
            "\n   After every 3 completed tasks, run a deploy checkpoint:\n"
            "   - `flyctl deploy --app <app-name>` (same app from Checkpoint 1)\n"
            "   - Verify health endpoint returns 200\n"
            "   - If fails, send deploy logs to **fixer**, redeploy (max 3)\n"
        )

    deploy_pre_review = ""
    if deploy_enabled:
        deploy_pre_review = (
            "\n### Deploy Checkpoint 3: Full Deploy (before review)\n"
            "1. `flyctl deploy --app <app-name>` (same app)\n"
            "2. Verify health check passes\n"
            "3. Note the deployed URL — the reviewer will run e2e tests against it\n\n"
        )

    db_note = ""
    if has_db:
        db_note = (
            "\nThis project has a database. The reviewer will include "
            "database schema and query review in its pass.\n"
        )

    return f"""\
You are the orchestrator for building a complete software project from a PRD.
You have 5 specialized subagents — delegate work to them and coordinate the build.

## Your Responsibilities

1. **Read and understand** the PRD below
2. **Delegate phases** to the appropriate subagents in order
3. **Verify outputs** using the verification scripts after each phase
4. **Course-correct** if verification fails — send specific issues to the **fixer**
5. **Track progress** — commit and push after each verified phase
6. **Never trust claims** — run `npm run build` and `npm test` YOURSELF after every subagent
{skip_instructions}
## Verification Scripts

After each phase, run the appropriate verification command and READ the output:
```bash
# Architecture verification
python -m src.orchestrator.verifier architecture

# Scaffold verification
python -m src.orchestrator.verifier scaffold

# Task verification (after each builder task)
python -m src.orchestrator.verifier task "<task_name>"

# Review verification
python -m src.orchestrator.verifier review
```
Each outputs JSON: {{"passed": true/false, "issues": [...]}}
If passed=false, send the issues to the **fixer** subagent, then re-verify.

## Pipeline Phases

### Phase 1: Architecture + Planning
1. Send the PRD to the **architect** subagent
   - It produces BOTH docs/ARCHITECTURE.md and docs/BUILD_PLAN.md
2. Run architecture verification: `python -m src.orchestrator.verifier architecture`
3. If verification fails, provide the issues to **architect** and re-run
4. Read docs/BUILD_PLAN.md — verify tasks have acceptance criteria
5. Commit: `git add -A && git commit -m "docs: add architecture and build plan"`
6. Push: `git push origin {branch_name}`

### Phase 2: Scaffold
1. Send to the **scaffolder** subagent
2. Run scaffold verification: `python -m src.orchestrator.verifier scaffold`
3. If fails, send issues to **fixer**, then re-verify (max 3 attempts)
4. Commit: `git add -A && git commit -m "chore: scaffold project structure"`
5. Push: `git push origin {branch_name}`
{deploy_checkpoint_instructions}
### Phase 3: Build (implement each task)
Read docs/BUILD_PLAN.md and implement tasks in order:
1. For each task, send it to the **builder** subagent with:
   - Task number, name, description, acceptance criteria
   - List of already-completed tasks for context
   - Reminder: "Write a failing API integration test FIRST, then implement"
2. After the builder completes, verify:
   - Run `npm run build` — read the output, confirm exit 0
   - Run `npm test` — read the output, confirm 0 failures
   - Run task verification: `python -m src.orchestrator.verifier task "<task_name>"`
   - If ANY fails, send issues to **fixer**, re-verify (max 3 attempts per task)
3. Spot-check the builder's work:
   - Read the test file — does it test real behavior (not mocks)?
   - Read the implementation — is it wired to the DB and routes?
   - If you find TODO stubs or unwired code, send back to **builder** with specific feedback
4. Commit ONLY after verified: `git add -A && git commit -m "feat: <task name>"`
5. Push: `git push origin {branch_name}`
{deploy_midpoint}
### Pre-Review: User Flow Audit
Before starting reviews, verify every key user flow yourself:
1. Read docs/ARCHITECTURE.md and list every user-facing flow
   (signup, login, create/read/update/delete for each resource, file upload, search, etc.)
2. For EACH flow, trace the full path through the code:
   - Frontend: is there a button/form/link that triggers it?
   - API call: does the frontend actually call the endpoint?
   - Backend: does the route handler do real work (not a stub/mock)?
   - Database: does it read/write real data (not hardcoded/mock)?
   - Response: does the result flow back to the UI?
3. Run: `grep -r "mock\\|Mock\\|hardcoded\\|TODO\\|FIXME\\|placeholder" src/`
   and investigate every hit.
4. For auth specifically: verify the REAL auth provider is configured,
   not anonymous/mock sign-in. Check for: signInAnonymously,
   mock-auth, fake-token, hardcoded-user, skip-auth.
5. Any broken flows → send to **fixer** with: "Flow X is broken because [specific gap]. Fix it."
6. Re-verify after fixes.
{deploy_pre_review}
### Phase 4: Review & E2E Tests
1. Send to the **reviewer** subagent — it performs code quality review,
   security audit,{" database review," if has_db else ""} Playwright e2e browser tests,
   and writes CODE_REVIEW.md + PR_DESCRIPTION.md
{db_note}
2. Fix-verify loop (max 5 rounds):
   a. Collect all issues from the reviewer (code, security, e2e failures)
   b. Send critical issues to **fixer**
   c. Run `npm run build` — confirm exit 0
   d. Run `npm test` — confirm 0 failures
   e. Re-run **reviewer** on any pages/areas that previously failed
   f. If new issues found, repeat from (a)
   g. If build/tests/e2e all pass clean → exit loop
   h. After 5 rounds, document remaining issues in CODE_REVIEW.md and proceed

3. Run review verification: `python -m src.orchestrator.verifier review`
4. Commit: `git add -A && git commit -m "docs: add review results"`
5. Push: `git push origin {branch_name}`

### Phase 5: Finalize
1. Final deploy: `flyctl deploy --app <app-name>` (if deploy enabled)
2. Commit: `git add -A && git commit -m "docs: finalize"`
3. Push: `git push origin {branch_name}`
4. Create PR: `gh pr create --title "<descriptive title>" --body-file docs/PR_DESCRIPTION.md --base main --head {branch_name}`

## Rules

- Run subagents **one at a time** — each phase depends on the previous
- After EVERY subagent, **run verification** and read the output — never skip this
- If verification fails, send specific issues to **fixer** and re-verify
- Maximum 3 retry attempts per verification failure before moving on
- Always commit and push after each verified phase
- The **reviewer** runs Playwright e2e against the deployed URL if available, otherwise localhost

## PRD

{prd_content}

Begin with Phase 1. Work through all phases in order.
"""
```

**Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('worker/src/orchestrator/runner.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add worker/src/orchestrator/runner.py
git commit -m "feat: rewrite orchestrator prompt with verification gates, deploy checkpoints, user flow audit"
```

---

## Task 7: Update `run_pipeline()` — model hierarchy + orchestrator system prompt

**Files:**
- Modify: `worker/src/orchestrator/runner.py:533-645` (the `run_pipeline` function)

**Step 1: Update the orchestrator agent launch**

In `run_pipeline()`, update the `run_agent` call to use Opus and the new system prompt:

```python
    try:
        result = await run_agent(
            prompt=orchestrator_prompt,
            system_prompt=(
                "You are a build orchestrator. You manage a team of 5 specialized "
                "subagents to build a complete software project from a PRD. "
                "Delegate work to subagents and verify their output.\n\n"
                "VERIFICATION RULE: Never trust a subagent's claim that something works. "
                "After every subagent completes:\n"
                "1. Run `npm run build` and `npm test` YOURSELF and read the actual output\n"
                "2. Run the appropriate verification script and read the JSON result\n"
                "3. If either shows failures, send the issues to the fixer before proceeding\n\n"
                "USER FLOW RULE: Before the review phase, trace every user-facing flow "
                "through the code. If any flow has a mock, stub, anonymous auth, or "
                "missing UI element — it is NOT complete. Send it to the fixer.\n\n"
                "Commit and push after each phase — but ONLY after verified green tests."
            ),
            allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob", "Task"],
            agents=subagents,
            cwd=repo_path,
            model="claude-opus-4-6",  # Orchestrator uses Opus for coordination judgment
            max_turns=200,
        )
```

**Step 2: Verify syntax**

Run: `python3 -c "import ast; ast.parse(open('worker/src/orchestrator/runner.py').read()); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add worker/src/orchestrator/runner.py
git commit -m "feat: use Opus for orchestrator, update system prompt with verification rules"
```

---

## Task 8: Add verifier CLI entrypoint

**Files:**
- Create: `worker/src/orchestrator/verifier_cli.py`

The orchestrator prompt tells the agent to run `python -m src.orchestrator.verifier <phase>`. We need a CLI wrapper.

**Step 1: Write the CLI module**

```python
# worker/src/orchestrator/verifier_cli.py
"""CLI entrypoint for phase verification.

Usage:
    python -m src.orchestrator.verifier architecture
    python -m src.orchestrator.verifier scaffold
    python -m src.orchestrator.verifier task "Task Name"
    python -m src.orchestrator.verifier review
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict

from src.orchestrator.verifier import PhaseVerifier


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python -m src.orchestrator.verifier <phase> [args]"}))
        sys.exit(1)

    phase = sys.argv[1]
    repo_path = "."  # Agent runs in the repo directory

    verifier = PhaseVerifier(repo_path)

    if phase == "architecture":
        result = verifier.verify_architecture()
    elif phase == "scaffold":
        result = verifier.verify_scaffold()
    elif phase == "task":
        task_name = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        result = verifier.verify_task(task_name)
    elif phase == "review":
        result = verifier.verify_review()
    else:
        print(json.dumps({"error": f"Unknown phase: {phase}"}))
        sys.exit(1)

    print(json.dumps({"passed": result.passed, "issues": result.issues}))
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
```

Also create `worker/src/orchestrator/__main__.py` to support `python -m src.orchestrator.verifier`:

Actually, the cleaner approach is to make the verifier module itself runnable. Add to the bottom of `worker/src/orchestrator/verifier.py`:

```python
if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: python -m src.orchestrator.verifier <phase> [args]"}))
        sys.exit(1)

    phase = sys.argv[1]
    verifier = PhaseVerifier(".")

    if phase == "architecture":
        result = verifier.verify_architecture()
    elif phase == "scaffold":
        result = verifier.verify_scaffold()
    elif phase == "task":
        task_name = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        result = verifier.verify_task(task_name)
    elif phase == "review":
        result = verifier.verify_review()
    else:
        print(json.dumps({"error": f"Unknown phase: {phase}"}))
        sys.exit(1)

    print(json.dumps({"passed": result.passed, "issues": result.issues}))
    sys.exit(0 if result.passed else 1)
```

**Step 2: Commit**

```bash
git add worker/src/orchestrator/verifier.py
git commit -m "feat: add CLI entrypoint for verifier module"
```

---

## Task 9: Clean up removed references

**Files:**
- Modify: `worker/src/orchestrator/runner.py` (imports and unused variables)
- Modify: `worker/src/orchestrator/component_loader.py` (remove dead methods)

**Step 1: Remove dead `component_loader` methods**

Remove these methods that are no longer called:
- `for_planner()` (lines 99-103)
- `for_security_reviewer()` (lines 135-140)
- `for_db_reviewer()` (lines 142-148)
- `for_e2e_runner()` (lines 150-156)

**Step 2: Remove unused imports from `runner.py`**

Check if `parse_build_plan` from `src.pipeline.models` is still used. If the orchestrator agent reads BUILD_PLAN.md directly (it does), this import can be removed.

**Step 3: Verify syntax on both files**

Run: `python3 -c "import ast; ast.parse(open('worker/src/orchestrator/runner.py').read()); ast.parse(open('worker/src/orchestrator/component_loader.py').read()); print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add worker/src/orchestrator/runner.py worker/src/orchestrator/component_loader.py
git commit -m "chore: remove dead methods and unused imports from consolidation"
```

---

## Task 10: Final integration verification

**Step 1: Run all tests**

```bash
cd /Users/finnerz/Mlabs-SOD && python -m pytest worker/tests/ -v
```
Expected: All tests pass

**Step 2: Verify full syntax**

```bash
python3 -c "
import ast
for f in [
    'worker/src/orchestrator/runner.py',
    'worker/src/orchestrator/verifier.py',
    'worker/src/orchestrator/progress.py',
    'worker/src/orchestrator/component_loader.py',
    'worker/src/pipeline/deployer.py',
]:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"
```
Expected: All files OK

**Step 3: Verify agent model assignments**

Grep to confirm model hierarchy:
```bash
grep -n 'model=' worker/src/orchestrator/runner.py
```
Expected:
- architect: `model="opus"`
- scaffolder: `model="sonnet"`
- builder: `model="sonnet"`
- fixer: `model="sonnet"`
- reviewer: `model="opus"`
- orchestrator run_agent call: `model="claude-opus-4-6"`

**Step 4: Commit any final fixes**

```bash
git add -A && git commit -m "chore: final integration verification"
```

---

## Summary

| Task | Creates/Modifies | What |
|------|-----------------|------|
| 1 | Create `verifier.py` + tests | StructuralChecker + PhaseVerifier — all verification logic |
| 2 | Modify `deployer.py` + tests | `deploy_checkpoint()` for lightweight redeploys |
| 3 | Modify `progress.py` + tests | Track deploy_app_name and deploy_url |
| 4 | Modify `component_loader.py` + tests | Consolidate loader methods for 5-agent architecture |
| 5 | Modify `runner.py` | Rewrite `_build_subagents()` — 11 → 5 agents with model hierarchy |
| 6 | Modify `runner.py` | Rewrite `_build_orchestrator_prompt()` — new phases + verification + deploy checkpoints |
| 7 | Modify `runner.py` | Update `run_pipeline()` — Opus model + new system prompt |
| 8 | Modify `verifier.py` | Add CLI entrypoint for `python -m src.orchestrator.verifier` |
| 9 | Modify `runner.py` + `component_loader.py` | Remove dead code from consolidation |
| 10 | All files | Integration verification — tests, syntax, model assignments |

**Dependency order:** Tasks 1-4 are independent (can parallelize). Task 5 depends on 4. Task 6-7 depend on 5. Task 8 depends on 1. Task 9 depends on 5. Task 10 depends on all.
