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
        ([sys.executable, "-m", "tools.contract_check", "--root", str(pack_root), "--full"], {"cwd": pack_root, "check": False}),
        ([sys.executable, "-m", "tools.render_contract_docs", "--root", str(pack_root), "--check"], {"cwd": pack_root, "check": False}),
        ([sys.executable, "-m", "pytest", "-q"], {"cwd": pack_root, "check": False}),
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
