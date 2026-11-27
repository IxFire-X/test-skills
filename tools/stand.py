"""The stand of a module (W3.2) and its shared data: the rules of the cases and the tests at a stand, the module of the attempt by the frozen
``.skillsrc``, whether its data are shared and whether it tests against a stand, and the line of ``ingest`` on tests skipped for want of the
address.  Moved out of ``step_size`` (27.11) as it was: ``step_size.step_modes`` and ``with_test_env`` call it by the attribute of this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

STAND_CASES_RULE = ("уровень автотестов — стенд: HTTP-шаг берёт адрес из переменной окружения {name} — base_url_source "
                    "{{kind: environment, name: {name}}}; другое имя submit отклонит (STAND_URL_NAME)")
STAND_TESTS_RULE = "уровень автотестов — стенд: тесты ходят только во внешний API по адресу из переменной {name} (System.getenv); без MockMvc, SpringBootTest и классов приложения"


def _frozen_module(project: Path, root: Path, attempt: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The attempt's module in the ``.skillsrc`` the run froze (the live file while its bytes are those of the baseline); None on a drifted or unreadable file."""
    from tools.pilot_state import frozen_skillsrc_matches
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc

    if frozen_skillsrc_matches(project, root, attempt) is not True:
        return None
    try:
        modules = normalize_skillsrc(load_skillsrc(Path(project) / ".skillsrc"))["modules"]
    except (OSError, SkillsrcError, ValueError, KeyError, TypeError):
        return None
    return next((item for item in modules if item.get("root") == attempt.get("module")), None)


SHARED_DATA_DEFAULT = True  # a module without ``shared_data``: False — its own environment (A); True — shared, ``"shared_data": false`` turns it off (B)


def module_shared(module: Mapping[str, Any]) -> bool:
    """The data of *module* are shared — other people's and demo records live there: its ``shared_data``; without the key a module at a stand is,
    any other is ``SHARED_DATA_DEFAULT``."""
    from tools.skillsrc_manifest import module_test_level

    if isinstance(module.get("shared_data"), bool):
        return bool(module["shared_data"])
    return (module_test_level(module) or ("",))[0] == "stand" or SHARED_DATA_DEFAULT


def shared_data(project: Path, root: Path, attempt: Mapping[str, Any]) -> bool:
    """:func:`module_shared` of the attempt's module by the frozen ``.skillsrc``.  Any delivery and profile (the cases of a cases-only run keep the
    same rule); a drifted or unreadable file is ``SHARED_DATA_DEFAULT`` — a change after the scan never turns the rule off silently."""
    try:
        module = _frozen_module(project, root, attempt)
    except (OSError, KeyError, TypeError, ValueError):
        module = None
    return SHARED_DATA_DEFAULT if module is None else module_shared(module)


def module_stand(project: Path, root: Path, attempt: Mapping[str, Any]) -> dict[str, Any] | None:
    """``{"service_url_env": name, "tests_only": bool}`` when the attempt's module tests against a stand (``test.level`` ``stand``, declared or
    derived) by the ``.skillsrc`` the run froze: the live file counts only while its bytes are those of the baseline.  The name of the variable
    only — the package never reads its value.  None in a delivery without autotests, a cases-only run, at ``in-process``, on a drifted file."""
    from tools.execution_gate import execution_available
    from tools.skillsrc_manifest import module_test_level

    if not execution_available() or attempt.get("policy_profile") == "cases-only-v1":
        return None
    module = _frozen_module(project, root, attempt)
    if module is None or (module_test_level(module) or ("",))[0] != "stand" or not module.get("service_url_env"):
        return None
    return {"service_url_env": str(module["service_url_env"]), "tests_only": bool((module.get("paths") or {}).get("context"))}


def stand_skip_note(project: Path, root: Path, attempt: Mapping[str, Any], report: Mapping[str, Any], verdict: str | None) -> dict[str, str]:
    """The line of ``ingest`` for a module at a stand whose tests JUnit skipped (``@EnabledIfEnvironmentVariable``: the variable was not set):
    the verdict is not known (``run_tests.classify_evidence``: a skipped test is never a pass), the line names the variable as a guess: the
    reason of a skip is not kept (``run_tests._testcase_status`` takes the tag only), so any skip may be another one."""
    if verdict != "UNKNOWN" or not any(row.get("status") == "SKIPPED" for row in report.get("execution_evidence") or []):
        return {}
    stand = module_stand(project, root, attempt)
    return {"stand_note": f"часть тестов пропущена; если не была задана переменная {stand['service_url_env']} — задайте её и выполните "
                          "напечатанную команду ещё раз"} if stand else {}
