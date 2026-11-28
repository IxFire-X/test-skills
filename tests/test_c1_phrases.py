"""The words of the C1 fixes (campaign v0.8), word for word: the dry run in HOW-TO-USE (approved by the user 28.11), the fix of a refused
fragment with several diagnostics, the help of the flag --dry-run."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_how_to_use_says_what_a_dry_run_writes() -> None:  # approved 28.11 («Утверждаю»), HOW-TO-USE.md:278–279
    text = re.sub(r"\s+", " ", (ROOT / "HOW-TO-USE.md").read_text(encoding="utf-8"))
    assert ("`submit --dry-run` проверяет ответ и не записывает его — журнал прогона остаётся как был; отказ проверки отчёт посчитает "
            "отдельно (строка в `dry-run-refusals.jsonl` в папке прогона).") in text


def test_the_fix_of_several_diagnostics_names_the_retry_once() -> None:
    from tools.model_steps import _fragment_fix

    own = "исправь все 6 диагностик из diagnostics за один заход (у каждой свой fix) и подай фрагмент единственным повтором стадии"
    retry = "отдай исправленный фрагмент единственным повтором стадии (--transport-attempts 2), ничего не записано"
    assert _fragment_fix(own, retry) == ("исправь все 6 диагностик из diagnostics за один заход (у каждой свой fix) и подай фрагмент "
                                         "единственным повтором стадии (--transport-attempts 2), ничего не записано")


def test_the_help_of_the_dry_run_flag() -> None:
    from tools.api import _HELP

    assert _HELP[("submit", "dry_run")] == "проверить ответ, не записывая его (отказ попадёт в отчёт)"
