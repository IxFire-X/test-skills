import subprocess
import sys
from pathlib import Path


def test_ci_gate_runs_the_required_checks_in_order(pack_root: Path, monkeypatch) -> None:
    from tools import ci_gate

    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(ci_gate.subprocess, "run", run)

    assert ci_gate.main(["--root", str(pack_root)]) == 0
    assert calls == [
        ([sys.executable, "-m", "tools.contract_check", "--root", str(pack_root), "--full"], {"cwd": pack_root, "check": False, "timeout": ci_gate.CHECK_TIMEOUT_SECONDS}),
        ([sys.executable, "-m", "tools.render_contract_docs", "--root", str(pack_root), "--check"], {"cwd": pack_root, "check": False, "timeout": ci_gate.CHECK_TIMEOUT_SECONDS}),
        ([sys.executable, "-m", "pytest", "-q"], {"cwd": pack_root, "check": False, "timeout": ci_gate.PYTEST_TIMEOUT_SECONDS}),
    ]


def test_ci_gate_stops_after_the_first_failed_check(pack_root: Path, monkeypatch) -> None:
    from tools import ci_gate

    statuses = iter((0, 1, 0))
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, next(statuses))

    monkeypatch.setattr(ci_gate.subprocess, "run", run)

    assert ci_gate.main(["--root", str(pack_root)]) == 1
    assert calls == [
        [sys.executable, "-m", "tools.contract_check", "--root", str(pack_root), "--full"],
        [sys.executable, "-m", "tools.render_contract_docs", "--root", str(pack_root), "--check"],
    ]


def test_ci_gate_reports_a_missing_runner_as_not_runnable(pack_root: Path, monkeypatch, capsys) -> None:
    from tools import ci_gate

    def run(command, **kwargs):
        raise OSError("runner unavailable")

    monkeypatch.setattr(ci_gate.subprocess, "run", run)

    assert ci_gate.main(["--root", str(pack_root)]) == 2
    assert "NOT_RUNNABLE: runner unavailable" in capsys.readouterr().err


def test_ci_gate_reports_a_missing_required_tool_as_not_runnable(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import ci_gate

    monkeypatch.setattr(ci_gate.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not run")))

    assert ci_gate.main(["--root", str(tmp_path)]) == 2
    assert "NOT_RUNNABLE: missing tools/contract_check.py" in capsys.readouterr().err



def test_the_workflow_gives_the_java_tests_a_jdk(pack_root: Path) -> None:
    """Independent review 2.2: without TEST_SKILLS_JAVA_HOME the Maven/PIT tests are skipped in GitHub Actions."""
    import re

    workflow = (pack_root / ".github" / "workflows" / "portable.yml").read_text(encoding="utf-8")
    setup = re.search(r"uses: actions/setup-java@([0-9a-f]{40}) # v[0-9.]+\n\s+with:\n\s+distribution: temurin\n\s+java-version: '17'", workflow)
    assert setup, "setup-java must be pinned by a commit SHA"
    gate = workflow.index("python -m tools.ci_gate --root .")
    assert workflow.index("actions/setup-java@") < gate
    assert "TEST_SKILLS_JAVA_HOME: ${{ env.JAVA_HOME }}" in workflow[gate:gate + 200]
