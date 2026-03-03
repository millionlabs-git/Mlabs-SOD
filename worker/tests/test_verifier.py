"""Tests for StructuralChecker methods in verifier.py."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.orchestrator.verifier import (
    StructuralChecker,
    VerifyResult,
    _is_test_file,
    _is_source_file,
    _is_skipped_dir,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# VerifyResult
# ---------------------------------------------------------------------------

class TestVerifyResult:
    def test_passed_result(self):
        r = VerifyResult(passed=True)
        assert r.passed is True
        assert r.issues == []

    def test_failed_result_with_issues(self):
        r = VerifyResult(passed=False, issues=["problem 1", "problem 2"])
        assert r.passed is False
        assert len(r.issues) == 2

    def test_to_json(self):
        r = VerifyResult(passed=True, issues=[])
        import json
        data = json.loads(r.to_json())
        assert data["passed"] is True
        assert data["issues"] == []


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_is_source_file(self):
        assert _is_source_file(Path("app.ts")) is True
        assert _is_source_file(Path("index.tsx")) is True
        assert _is_source_file(Path("server.js")) is True
        assert _is_source_file(Path("main.py")) is True
        assert _is_source_file(Path("main.go")) is True
        assert _is_source_file(Path("readme.md")) is False
        assert _is_source_file(Path("image.png")) is False

    def test_is_test_file(self):
        assert _is_test_file(Path("tests/foo.ts")) is True
        assert _is_test_file(Path("__tests__/bar.tsx")) is True
        assert _is_test_file(Path("src/app.test.ts")) is True
        assert _is_test_file(Path("src/app.spec.ts")) is True
        assert _is_test_file(Path("test_utils.py")) is True
        assert _is_test_file(Path("src/utils.ts")) is False

    def test_is_skipped_dir(self):
        assert _is_skipped_dir(Path("node_modules/foo/bar.ts")) is True
        assert _is_skipped_dir(Path(".git/config")) is True
        assert _is_skipped_dir(Path("src/app.ts")) is False


# ---------------------------------------------------------------------------
# StructuralChecker.check_no_stubs
# ---------------------------------------------------------------------------

class TestCheckNoStubs:
    def test_no_stubs_clean(self, tmp_path: Path):
        _write(tmp_path / "src" / "app.ts", "const x = 1;\nconsole.log(x);\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert issues == []

    def test_detects_todo(self, tmp_path: Path):
        _write(tmp_path / "src" / "app.ts", "const x = 1; // TODO: finish this\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert len(issues) == 1
        assert "TODO" in issues[0]

    def test_detects_fixme(self, tmp_path: Path):
        _write(tmp_path / "src" / "app.py", "x = 1  # FIXME broken\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert len(issues) == 1
        assert "FIXME" in issues[0]

    def test_detects_placeholder(self, tmp_path: Path):
        _write(tmp_path / "lib" / "util.js", "return 'placeholder value';\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert len(issues) == 1
        assert "placeholder" in issues[0].lower()

    def test_detects_xxx(self, tmp_path: Path):
        _write(tmp_path / "src" / "app.ts", "// XXX: needs work\nconst a = 1;\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert len(issues) == 1
        assert "XXX" in issues[0]

    def test_skips_test_files(self, tmp_path: Path):
        _write(tmp_path / "tests" / "app.test.ts", "// TODO: add more tests\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert issues == []

    def test_skips_node_modules(self, tmp_path: Path):
        _write(tmp_path / "node_modules" / "pkg" / "index.js", "// TODO\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert issues == []

    def test_multiple_stubs(self, tmp_path: Path):
        content = "// TODO: first\nconst x = 1;\n// FIXME: second\n"
        _write(tmp_path / "src" / "app.ts", content)
        checker = StructuralChecker(tmp_path)
        issues = checker.check_no_stubs()
        assert len(issues) == 2


# ---------------------------------------------------------------------------
# StructuralChecker.check_mock_patterns
# ---------------------------------------------------------------------------

class TestCheckMockPatterns:
    def test_clean(self, tmp_path: Path):
        _write(tmp_path / "src" / "auth.ts", "import { signIn } from 'firebase';\n")
        checker = StructuralChecker(tmp_path)
        assert checker.check_mock_patterns() == []

    def test_detects_sign_in_anonymously(self, tmp_path: Path):
        _write(tmp_path / "src" / "auth.ts", "await signInAnonymously(auth);\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_mock_patterns()
        assert len(issues) == 1
        assert "signInAnonymously" in issues[0]

    def test_detects_fake_token(self, tmp_path: Path):
        _write(tmp_path / "src" / "api.ts", 'const token = "fake-token";\n')
        checker = StructuralChecker(tmp_path)
        issues = checker.check_mock_patterns()
        assert len(issues) == 1

    def test_allows_mocks_in_test_files(self, tmp_path: Path):
        _write(
            tmp_path / "__tests__" / "auth.test.ts",
            "signInAnonymously(mockAuth);\n",
        )
        checker = StructuralChecker(tmp_path)
        assert checker.check_mock_patterns() == []

    def test_detects_mock_function(self, tmp_path: Path):
        _write(tmp_path / "src" / "setup.js", "const fn = mockFunction(handler);\n")
        checker = StructuralChecker(tmp_path)
        issues = checker.check_mock_patterns()
        assert len(issues) == 1


# ---------------------------------------------------------------------------
# StructuralChecker.check_route_wiring
# ---------------------------------------------------------------------------

class TestCheckRouteWiring:
    def test_all_routes_present(self, tmp_path: Path):
        _write(
            tmp_path / "src" / "routes.ts",
            'app.get("/api/users", handler);\napp.post("/api/posts", handler);\n',
        )
        checker = StructuralChecker(tmp_path)
        missing = checker.check_route_wiring(["/api/users", "/api/posts"])
        assert missing == []

    def test_missing_route(self, tmp_path: Path):
        _write(tmp_path / "src" / "routes.ts", 'app.get("/api/users", handler);\n')
        checker = StructuralChecker(tmp_path)
        missing = checker.check_route_wiring(["/api/users", "/api/posts"])
        assert len(missing) == 1
        assert "/api/posts" in missing[0]

    def test_empty_expected(self, tmp_path: Path):
        checker = StructuralChecker(tmp_path)
        assert checker.check_route_wiring([]) == []


# ---------------------------------------------------------------------------
# StructuralChecker.check_model_wiring
# ---------------------------------------------------------------------------

class TestCheckModelWiring:
    def test_all_models_present(self, tmp_path: Path):
        _write(
            tmp_path / "src" / "models.ts",
            "interface User { id: string }\ninterface Post { title: string }\n",
        )
        checker = StructuralChecker(tmp_path)
        missing = checker.check_model_wiring(["User", "Post"])
        assert missing == []

    def test_missing_model(self, tmp_path: Path):
        _write(tmp_path / "src" / "models.ts", "interface User { id: string }\n")
        checker = StructuralChecker(tmp_path)
        missing = checker.check_model_wiring(["User", "Post"])
        assert len(missing) == 1
        assert "Post" in missing[0]

    def test_empty_expected(self, tmp_path: Path):
        checker = StructuralChecker(tmp_path)
        assert checker.check_model_wiring([]) == []


# ---------------------------------------------------------------------------
# StructuralChecker.check_page_wiring
# ---------------------------------------------------------------------------

class TestCheckPageWiring:
    def test_all_pages_present(self, tmp_path: Path):
        _write(
            tmp_path / "src" / "pages.tsx",
            'export function Dashboard() {}\nexport function Settings() {}\n',
        )
        checker = StructuralChecker(tmp_path)
        missing = checker.check_page_wiring(["Dashboard", "Settings"])
        assert missing == []

    def test_missing_page(self, tmp_path: Path):
        _write(tmp_path / "src" / "pages.tsx", "export function Dashboard() {}\n")
        checker = StructuralChecker(tmp_path)
        missing = checker.check_page_wiring(["Dashboard", "Settings"])
        assert len(missing) == 1
        assert "Settings" in missing[0]

    def test_empty_expected(self, tmp_path: Path):
        checker = StructuralChecker(tmp_path)
        assert checker.check_page_wiring([]) == []


# ---------------------------------------------------------------------------
# StructuralChecker.check_test_has_assertions
# ---------------------------------------------------------------------------

class TestCheckTestHasAssertions:
    def test_file_with_assertions(self, tmp_path: Path):
        tf = _write(
            tmp_path / "test_app.ts",
            'test("adds 1+1", () => { expect(1+1).toBe(2); });\n',
        )
        checker = StructuralChecker(tmp_path)
        assert checker.check_test_has_assertions(tf) == []

    def test_file_without_assertions(self, tmp_path: Path):
        tf = _write(
            tmp_path / "test_app.ts",
            'test("placeholder", () => { console.log("hello"); });\n',
        )
        checker = StructuralChecker(tmp_path)
        issues = checker.check_test_has_assertions(tf)
        assert len(issues) == 1
        assert "No assertions" in issues[0]

    def test_nonexistent_file(self, tmp_path: Path):
        checker = StructuralChecker(tmp_path)
        issues = checker.check_test_has_assertions(tmp_path / "nope.ts")
        assert len(issues) == 1
        assert "does not exist" in issues[0]

    def test_python_assert(self, tmp_path: Path):
        tf = _write(
            tmp_path / "test_app.py",
            "def test_add():\n    assert 1 + 1 == 2\n",
        )
        checker = StructuralChecker(tmp_path)
        assert checker.check_test_has_assertions(tf) == []

    def test_pytest_raises(self, tmp_path: Path):
        tf = _write(
            tmp_path / "test_err.py",
            "def test_err():\n    with pytest.raises(ValueError):\n        raise ValueError\n",
        )
        checker = StructuralChecker(tmp_path)
        assert checker.check_test_has_assertions(tf) == []
