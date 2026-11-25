"""v0.7.2 t3: what status and finish say to a person — the outcome of a set of cases in a line, and the refusal that stopped a run before its attempt."""
from __future__ import annotations

from pathlib import Path

from tools import json_cli, pilot_state, run_pipeline

AUTHORIZATION = {"request_id": "request-1", "execution_requested": False}


def _run(tmp_path: Path) -> Path:
    return Path(pilot_state.create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])


def _status(root: Path) -> dict:
    return run_pipeline._durable_status(root.parents[1], root, stage="status")


def test_status_names_the_last_refusal_before_an_attempt(tmp_path: Path) -> None:
    root = _run(tmp_path)
    json_cli.record_refusal(root, "scan", "SNAPSHOT_DOCS")
    json_cli.record_refusal(root, "scan", "SCAN_RUN_IN_PROGRESS")
    status = _status(root)
    assert "SCAN_RUN_IN_PROGRESS (scan," in status["stop_detail"] and status["stop_human"]


def test_status_keeps_the_old_phrase_without_a_refusal(tmp_path: Path) -> None:
    assert "may have no persisted cause" in _status(_run(tmp_path))["stop_detail"]


def test_a_refusal_line_the_journal_does_not_confirm_is_not_named(tmp_path: Path) -> None:
    root = _run(tmp_path)
    (root / "refusals.jsonl").write_text('{"at": "2026-11-25T00:00:00Z", "code": "MADE_UP", "command": "scan"}\n', encoding="utf-8")
    assert "MADE_UP" not in _status(root)["stop_detail"]


def test_a_whole_set_names_the_csv() -> None:
    text = run_pipeline.human_summary({"outcome": "ACCEPTED_WHOLE", "generated": 12, "accepted": 12, "removed": 0}, "p/x.zephyr-scale.csv", True)
    assert text == "Кейсы приняты (12 из 12), файл для Zephyr готов: p/x.zephyr-scale.csv."


def test_a_partial_set_names_the_rejected() -> None:
    acceptance = {"outcome": "PARTIAL", "generated": 12, "accepted": 9, "removed": 3}
    text = run_pipeline.human_summary(acceptance, "p/x.zephyr-scale.csv", True)
    assert text.startswith("Кейсы приняты частично (9 из 12): 3 не вошли в файл (отклонены ревью или сняты вместе со своей группой); файл") and "p/x.REJECTED-CASES.html" in text


def test_a_rework_says_there_is_no_file() -> None:
    text = run_pipeline.human_summary({"outcome": "REWORK", "reason": "OVER_SHARE", "generated": 12, "accepted": 0, "removed": 5}, None, False)
    assert text == "Кейсы требуют доработки: ревью отклонило 5 из 12 (больше допустимой доли), файл для Zephyr не создан."


def test_an_accepted_set_whose_csv_is_gone_says_so() -> None:
    text = run_pipeline.human_summary({"outcome": "ACCEPTED_WHOLE", "generated": 2, "accepted": 2, "removed": 0}, "p/x.zephyr-scale.csv", False)
    assert text == "Кейсы приняты (2 из 2), но файла для Zephyr в прогоне нет: p/x.zephyr-scale.csv."


def _command(capsys, *argv: str) -> dict:
    import json

    run_pipeline.main(list(argv))
    return json.loads(capsys.readouterr().out)


def test_status_and_finish_of_a_reviewed_cases_only_run_say_the_outcome(tmp_path: Path, capsys, monkeypatch) -> None:
    from tests.test_review_canaries import _answer, _generated_run, _open, _select

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    _select(run, _answer(run, "ПРИНЯТО"))
    capsys.readouterr()
    where = ("--project", str(run["run_root"].parents[1]), "--run", run["run_root"].name)
    status, finish = _command(capsys, "status", *where), _command(capsys, "finish", *where)
    assert status["human_summary"].startswith("Кейсы приняты (") and finish["human_summary"] == status["human_summary"] and finish["accepted"] is False
