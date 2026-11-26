"""v0.9 style samples (R&D-15 step 1): up to three cases of the team (``.skillsrc`` ``style.samples``) are an input of every batch
of the generator — the form, never the facts; a fact of a sample the requirements do not give is the hint CV_STYLE_SAMPLE."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

SAMPLES_PATH = "services/api/docs/style.csv"


def _case(title: str, data: str = "Имя: Анна") -> dict:
    return {"row": 2, "key": "PC-T1", "owner": "Иван Петров", "title": title, "objective": "Проверить карточку", "preconditions": "",
            "steps": [{"row": 2, "action": "Открыть карточку", "test_data": data, "expected": "Карточка открыта"}]}


def _csv() -> bytes:
    from tests.test_reference_cases import _csv as csv, _example

    return csv(_example())


def _with_style(project: Path, path: str = SAMPLES_PATH) -> None:
    manifest = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    (project / ".skillsrc").write_text(json.dumps({**manifest, "style": {"samples": path}}, ensure_ascii=False), encoding="utf-8")
    (project / path).write_bytes(_csv())


def test_the_skillsrc_takes_style_samples_and_nothing_else_there() -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes
    from tests.test_first_run import _skillsrc

    manifest = json.loads(_skillsrc())
    assert parse_skillsrc_bytes(json.dumps({**manifest, "style": {"samples": "cases/style.csv"}}).encode())["style"] == {"samples": "cases/style.csv"}
    with pytest.raises(SkillsrcError):
        parse_skillsrc_bytes(json.dumps({**manifest, "style": {"profile": "x"}}).encode())


def test_three_short_cases_are_taken_and_the_scan_says_so() -> None:
    from tools.style_samples import select

    samples, warnings = select([_case("Длинный", "x" * 2100), *(_case(f"Кейс {number}") for number in range(3)), _case("После трёх", "x" * 2100)], "style.csv")
    assert [sample["title"] for sample in samples] == ["Кейс 0", "Кейс 1", "Кейс 2"]
    assert warnings == ["Образцы стиля: взято 3 кейса из style.csv — модель возьмёт форму, не факты.",
                        "Образец «Длинный» длиннее 2 КБ — не взят.", "В style.csv кейсов больше трёх — взяты первые три."]


def test_a_sample_has_the_human_fields_only() -> None:
    from tools.style_samples import select

    [sample], _warnings = select([_case("Кейс")], "style.csv")
    assert set(sample) == {"title", "objective", "preconditions", "steps"} and "Иван" not in json.dumps(sample, ensure_ascii=False)


def test_scan_publishes_the_samples_and_keeps_the_csv_out_of_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan
    from tools import pilot_state

    project = _python_project(tmp_path, runtime=False)
    _with_style(project)
    code, payload = _scan(project, monkeypatch)
    assert code == 0 and "Образцы стиля: взято 3 кейса из services/api/docs/style.csv — модель возьмёт форму, не факты." in payload["warnings"], payload
    root = project / ".pilot-runs" / payload["run_id"]
    attempt_id = pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]
    samples = pilot_state.read_style_samples(root, attempt_id)
    assert samples["path"] == SAMPLES_PATH and len(samples["cases"]) == 3
    selected = [file["project_path"] for path in (root / "context-selections").rglob("*.json") for file in json.loads(path.read_bytes())["files"]]
    assert SAMPLES_PATH not in selected and selected


def test_a_large_samples_file_is_refused_before_the_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan

    project = _python_project(tmp_path, runtime=False)
    _with_style(project)
    (project / SAMPLES_PATH).write_bytes(b"x" * (256 * 1024 + 1))
    code, payload = _scan(project, monkeypatch)
    assert code == 2 and "образцы стиля" in payload["detail"] and not (project / ".pilot-runs").exists(), payload


def test_the_samples_are_an_input_of_the_batch_before_its_skeleton(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.helpers import prepare_stage
    from tests.test_cases_import import _to_generator
    from tools import pilot_state, run_scan, step_size

    monkeypatch.setattr(run_scan, "_style_samples", lambda _project: ("cases/style.csv", b"csv", [{"title": "Кейс"}], []))
    run = _to_generator(tmp_path, monkeypatch)
    stage = f"tc-generator:{run['plan']['batches'][0]['batch_id']}"
    assert prepare_stage(run["root"], stage)[0] == 0
    state = pilot_state.derive_state(run["root"])
    expected = pilot_state._expected_model_inputs(run["project"].resolve(), run["root"].resolve(), state, state["attempts"][-1], stage)
    samples = pilot_state.read_style_samples(run["root"], run["attempt_id"])["digest"]
    assert [digest for digest, _source in expected][-2] == samples  # the skeleton of the import stays the last input
    found = step_size.request_files(run["root"], stage)
    assert any("style-samples" in Path(row["path"]).parts for row in found["files"]) and found["unresolved"] == []


def test_a_fact_of_a_sample_is_a_hint_and_a_word_of_form_is_not() -> None:
    from tests.test_reference_cases import _example
    from tools.style_samples import transferred

    document = _example()
    case = document["test_cases"][0]
    case["steps"][0]["test_data"] += " Город: Тверь."
    sample = {"title": "Открыть карточку", "objective": "", "preconditions": "", "steps": [{"action": "Открыть", "test_data": "Город: Тверь", "expected": ""}]}
    assert transferred(document, [sample], known="требования без города") == {case["case_id"]: ["тверь"]}
    assert transferred(document, [sample], known="Город Тверь из требования") == {}


def test_the_view_has_the_hint_only_with_a_carried_fact() -> None:
    from tests.test_reference_cases import _example
    from tools.review_view import render

    document = _example()
    assert render(document, style={}) == render(document)
    view = render(copy.deepcopy(document), style={"TC-1": ["тверь"]}).decode("utf-8")
    assert "- CV_STYLE_SAMPLE: в кейсах есть значения из образцов стиля" in view and "TC-1: «тверь»" in view


def test_how_to_use_says_the_approved_words() -> None:
    text = " ".join((Path(__file__).resolve().parents[1] / "HOW-TO-USE.md").read_text(encoding="utf-8").split())
    assert ("**Кейсы в стиле вашей команды.** Положите до трёх своих кейсов — экспорт Zephyr в CSV — и укажите путь в `.skillsrc`: "
            "`\"style\": {\"samples\": \"cases/style.csv\"}`. Модель возьмёт из них форму: как вы называете кейсы, пишете шаги, данные и "
            "ожидания. Факты из образцов она не берёт; если значение из образца всё же попало в кейс, ревьюер увидит это подсказкой. "
            "Берите кейсы на другие требования, чем у прогона.") in text
