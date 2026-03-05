"""Deterministic verification module — replaces LLM-based evaluation.

Provides StructuralChecker for file-system grep checks and PhaseVerifier
for orchestrating per-phase verification via shell commands + structural checks.
Output is JSON so the orchestrator can parse results programmatically.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STUB_PATTERNS: list[str] = ["TODO", "FIXME", "placeholder", "XXX"]

MOCK_PATTERNS: list[str] = [
    "signInAnonymously",
    "mock-auth",
    "fake-token",
    "hardcoded-user",
    "skip-auth",
    "mockFunction(",
]

SKIP_DIRS: set[str] = {
    "node_modules",
    ".git",
    ".next",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".claude",
    ".cache",
    "coverage",
    ".turbo",
}

SOURCE_EXTENSIONS: set[str] = {".ts", ".tsx", ".js", ".jsx", ".py", ".go"}

TEST_INDICATORS: set[str] = {
    "test",
    "tests",
    "spec",
    "specs",
    "__tests__",
    "__test__",
}

ASSERTION_PATTERNS: list[str] = [
    "expect(",
    "assert",
    "toBe(",
    "toEqual(",
    "toContain(",
    "toThrow(",
    "toHaveBeenCalled",
    "toMatch(",
    "should.",
    "assertEqual",
    "assertTrue",
    "assertFalse",
    "assertRaises",
    "assertIn",
    "pytest.raises",
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class VerifyResult:
    """Outcome of a verification step."""

    passed: bool
    issues: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_skipped_dir(path: Path) -> bool:
    """Return True if any component of *path* is in SKIP_DIRS."""
    return bool(SKIP_DIRS & set(path.parts))


def _is_source_file(path: Path) -> bool:
    return path.suffix in SOURCE_EXTENSIONS


def _is_test_file(path: Path) -> bool:
    """Heuristic: file lives under a test-related directory or has test/spec in name."""
    lower_parts = {p.lower() for p in path.parts}
    if lower_parts & TEST_INDICATORS:
        return True
    stem = path.stem.lower()
    return (
        stem.startswith("test_")
        or stem.endswith("_test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
        or stem.startswith("spec_")
        or stem.endswith("_spec")
    )


def _iter_source_files(root: Path, *, include_tests: bool = True) -> list[Path]:
    """Walk *root* and yield source files, skipping SKIP_DIRS."""
    results: list[Path] = []
    if not root.is_dir():
        return results
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories in-place so os.walk doesn't descend.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if not _is_source_file(fpath):
                continue
            if not include_tests and _is_test_file(fpath):
                continue
            results.append(fpath)
    return results


def _grep_patterns(files: list[Path], patterns: list[str]) -> list[str]:
    """Search *files* for any of *patterns* (case-insensitive). Return matches."""
    issues: list[str] = []
    compiled = [re.compile(re.escape(p), re.IGNORECASE) for p in patterns]
    for fpath in files:
        try:
            lines = fpath.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            for rx in compiled:
                if rx.search(line):
                    issues.append(f"{fpath}:{lineno}: {line.strip()}")
                    break  # one match per line is enough
    return issues


def _run_shell(cmd: str, cwd: str | Path, *, timeout: int = 300) -> tuple[int, str]:
    """Run *cmd* via shell and return (returncode, combined stdout+stderr)."""
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode, output


# ---------------------------------------------------------------------------
# StructuralChecker
# ---------------------------------------------------------------------------

class StructuralChecker:
    """File-system verification checks — no LLM calls."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- stub detection -----------------------------------------------------

    def check_no_stubs(self) -> list[str]:
        """Grep non-test source files for TODO/FIXME/placeholder/XXX patterns."""
        files = _iter_source_files(self.root, include_tests=False)
        return _grep_patterns(files, STUB_PATTERNS)

    # -- mock / auth bypass detection ----------------------------------------

    def check_mock_patterns(self) -> list[str]:
        """Grep non-test source files for mock/fake auth patterns."""
        files = _iter_source_files(self.root, include_tests=False)
        return _grep_patterns(files, MOCK_PATTERNS)

    # -- route wiring --------------------------------------------------------

    def check_route_wiring(self, expected_routes: Sequence[str]) -> list[str]:
        """Check that each expected route path appears in at least one source file."""
        if not expected_routes:
            return []
        files = _iter_source_files(self.root, include_tests=False)
        all_source = ""
        for fpath in files:
            try:
                all_source += fpath.read_text(errors="replace") + "\n"
            except OSError:
                continue
        missing: list[str] = []
        for route in expected_routes:
            if route not in all_source:
                missing.append(f"Missing route: {route}")
        return missing

    # -- model wiring --------------------------------------------------------

    def check_model_wiring(self, expected_models: Sequence[str]) -> list[str]:
        """Check that each expected model name appears in at least one source file."""
        if not expected_models:
            return []
        files = _iter_source_files(self.root, include_tests=False)
        all_source = ""
        for fpath in files:
            try:
                all_source += fpath.read_text(errors="replace") + "\n"
            except OSError:
                continue
        missing: list[str] = []
        for model in expected_models:
            if model not in all_source:
                missing.append(f"Missing model: {model}")
        return missing

    # -- page wiring ---------------------------------------------------------

    def check_page_wiring(self, expected_pages: Sequence[str]) -> list[str]:
        """Check that each expected page/route path appears in at least one source file."""
        if not expected_pages:
            return []
        files = _iter_source_files(self.root, include_tests=False)
        all_source = ""
        for fpath in files:
            try:
                all_source += fpath.read_text(errors="replace") + "\n"
            except OSError:
                continue
        missing: list[str] = []
        for page in expected_pages:
            if page not in all_source:
                missing.append(f"Missing page: {page}")
        return missing

    # -- test assertion check ------------------------------------------------

    def check_test_has_assertions(self, test_file: Path | str) -> list[str]:
        """Check that *test_file* contains real assertions."""
        test_file = Path(test_file)
        if not test_file.is_file():
            return [f"Test file does not exist: {test_file}"]
        try:
            content = test_file.read_text(errors="replace")
        except OSError as exc:
            return [f"Cannot read test file {test_file}: {exc}"]
        for pattern in ASSERTION_PATTERNS:
            if pattern in content:
                return []  # at least one assertion found
        return [f"No assertions found in {test_file}"]


# ---------------------------------------------------------------------------
# PhaseVerifier
# ---------------------------------------------------------------------------

class PhaseVerifier:
    """Orchestrates per-phase checks using StructuralChecker + shell commands."""

    def __init__(self, repo_root: Path | str) -> None:
        self.root = Path(repo_root)
        self.checker = StructuralChecker(self.root)

    # -- helpers -------------------------------------------------------------

    def _build_and_test(self) -> list[str]:
        """Run npm install, build, test. Return list of issues (empty = all good)."""
        issues: list[str] = []

        rc, out = _run_shell("npm install --prefer-offline", self.root)
        if rc != 0:
            issues.append(f"npm install failed (rc={rc}): {out[-500:]}")

        rc, out = _run_shell("npm run build", self.root)
        if rc != 0:
            issues.append(f"npm run build failed (rc={rc}): {out[-500:]}")

        rc, out = _run_shell("npm test", self.root)
        if rc != 0:
            issues.append(f"npm test failed (rc={rc}): {out[-500:]}")

        return issues

    def _file_exists(self, relpath: str) -> bool:
        return (self.root / relpath).is_file()

    def _file_has_content(self, relpath: str, needle: str) -> bool:
        fpath = self.root / relpath
        if not fpath.is_file():
            return False
        try:
            return needle in fpath.read_text(errors="replace")
        except OSError:
            return False

    # -- phase: architecture ------------------------------------------------

    def verify_architecture(self) -> VerifyResult:
        """Check ARCHITECTURE.md and BUILD_PLAN.md exist with required sections."""
        issues: list[str] = []

        for doc in ("docs/ARCHITECTURE.md", "docs/BUILD_PLAN.md"):
            if not self._file_exists(doc):
                issues.append(f"{doc} does not exist")
                continue
            content = (self.root / doc).read_text(errors="replace").strip()
            if len(content) < 100:
                issues.append(f"{doc} is too short ({len(content)} chars)")

        # ARCHITECTURE.md required content (check for key topics, not exact headings)
        if self._file_exists("docs/ARCHITECTURE.md"):
            content = (self.root / "docs/ARCHITECTURE.md").read_text(errors="replace").lower()
            for topic in ("data model", "api", "endpoint", "route"):
                if topic not in content:
                    issues.append(f"docs/ARCHITECTURE.md missing content about: {topic}")

        # BUILD_PLAN.md — tasks with acceptance criteria
        if self._file_exists("docs/BUILD_PLAN.md"):
            content = (self.root / "docs/BUILD_PLAN.md").read_text(errors="replace")
            if "acceptance criteria" not in content.lower():
                issues.append("docs/BUILD_PLAN.md tasks missing acceptance criteria")
            import re as _re
            task_count = len(_re.findall(r"##\s+Task\s+\d+", content))
            if task_count < 3:
                issues.append(f"docs/BUILD_PLAN.md has only {task_count} tasks (expected 8-15)")

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    # -- phase: scaffold ----------------------------------------------------

    def verify_scaffold(self) -> VerifyResult:
        """Build + test + structural wiring + no stubs + no mocks."""
        issues: list[str] = []

        issues.extend(self._build_and_test())
        issues.extend(self.checker.check_no_stubs())
        issues.extend(self.checker.check_mock_patterns())

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    # -- phase: task --------------------------------------------------------

    def verify_task(
        self,
        task_name: str,
        new_test_files: Sequence[str] | None = None,
    ) -> VerifyResult:
        """Build + test + test assertions + no stubs/mocks for a single task."""
        issues: list[str] = []

        issues.extend(self._build_and_test())

        # Validate test files have real assertions
        for tf in (new_test_files or []):
            tf_path = self.root / tf
            issues.extend(self.checker.check_test_has_assertions(tf_path))

        issues.extend(self.checker.check_no_stubs())
        issues.extend(self.checker.check_mock_patterns())

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    # -- phase: review ------------------------------------------------------

    def verify_review(self) -> VerifyResult:
        """CODE_REVIEW.md + PR_DESCRIPTION.md exist, final build + test."""
        issues: list[str] = []

        for doc in ("CODE_REVIEW.md", "PR_DESCRIPTION.md"):
            if not self._file_exists(doc):
                issues.append(f"{doc} does not exist")

        # Check no unfixed critical issues in CODE_REVIEW.md
        if self._file_exists("CODE_REVIEW.md"):
            content = (self.root / "CODE_REVIEW.md").read_text(errors="replace")
            # Look for critical markers that are not marked as fixed
            for line in content.splitlines():
                lower = line.lower()
                if "critical" in lower and "fixed" not in lower and "resolved" not in lower:
                    issues.append(f"Unfixed critical in CODE_REVIEW.md: {line.strip()}")

        # Check e2e screenshots directory exists and has files
        screenshots = self.root / "docs" / "screenshots"
        if not screenshots.is_dir():
            issues.append("docs/screenshots directory does not exist")
        elif not any(screenshots.iterdir()):
            issues.append("docs/screenshots directory is empty")

        issues.extend(self._build_and_test())

        return VerifyResult(passed=len(issues) == 0, issues=issues)

    # -- phase: email_setup -------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI: python -m src.orchestrator.verifier <phase> [--root DIR] [extra args]."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Deterministic phase verifier",
    )
    parser.add_argument(
        "phase",
        choices=["architecture", "scaffold", "task", "review", "email_setup"],
        help="Phase to verify",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root directory (default: cwd)",
    )
    parser.add_argument(
        "--task-name",
        default="unknown",
        help="Task name (for 'task' phase)",
    )
    parser.add_argument(
        "--test-files",
        nargs="*",
        default=[],
        help="New test files to validate (for 'task' phase)",
    )
    args = parser.parse_args()

    verifier = PhaseVerifier(args.root)

    if args.phase == "architecture":
        result = verifier.verify_architecture()
    elif args.phase == "scaffold":
        result = verifier.verify_scaffold()
    elif args.phase == "task":
        result = verifier.verify_task(args.task_name, args.test_files)
    elif args.phase == "review":
        result = verifier.verify_review()
    elif args.phase == "email_setup":
        result = verifier.verify_email_setup()
    else:
        result = VerifyResult(passed=False, issues=[f"Unknown phase: {args.phase}"])

    print(result.to_json())
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
