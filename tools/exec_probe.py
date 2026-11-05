"""P8a-1: the offline validate probe of ``ready`` (FaT-L) — starts one project process, so it lives with the execution files.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.ready import EXECUTION, _row


PROBE_TIMEOUT = 120


def _probe_runner(argv: Sequence[str], cwd: Path, *, timeout: int) -> Any:
    from tools.exec_runner import run_subprocess

    return run_subprocess(list(argv), Path(cwd), timeout=timeout)


_URL_USERINFO = re.compile(r"://[^/\s@]+@")


# F9 (8.D6-10): one attempt per run of word characters — what follows a key (a quote, a blank, «:», «=») is not one, so every keyword of the run ends at the same
# place and the first is enough (a line of «password» x 8 000 was k x n and did not finish).  The prefix before the keyword is now in group 1 and is kept.
_SECRET_VALUE = re.compile(r"(?i)((?<![\w.-])(?>[\w.-]*?(?:password|passwd|pwd|token|secret)[\w.-]*)['\"]?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;)]+)")


PROBE_LINE_CHARS = 200


def _scrub(line: str) -> str:
    """FaT-H2a: an [ERROR] line of the probe goes to the agent — no URL userinfo, no secret values, at most 200 chars."""
    line = _SECRET_VALUE.sub(r"\1***", _URL_USERINFO.sub("://***@", line))
    return line if len(line) <= PROBE_LINE_CHARS else line[:PROBE_LINE_CHARS - 1] + "…"


def build_probe(project: Path, module: Mapping[str, Any], runner: Any = None) -> dict[str, Any] | None:
    """FaT-L: the offline validate phase with the exec flags — whatever fails here fails the selected-tests run too."""
    from tools.execution_adapters import AdapterRequestError, MAVEN, SYSTEM_MAVEN, probe_command_for, probe_executable

    test = module.get("test") if isinstance(module.get("test"), Mapping) else None
    if test is None or test.get("adapter_id") not in {MAVEN, SYSTEM_MAVEN}:
        return None
    module_root = (Path(project).resolve() / str(module.get("root") or ".")).resolve()
    try:
        argv = probe_command_for(str(test["adapter_id"]), probe_executable(module_root, test), str(test.get("build_profile")))
    except (AdapterRequestError, KeyError, OSError):
        return None
    started = time.monotonic()
    outcome = (runner or _probe_runner)(argv, module_root, timeout=PROBE_TIMEOUT)
    seconds = round(time.monotonic() - started, 1)
    command = " ".join(f'"{part}"' if " " in part else part for part in argv)
    # FaT-H2a P1: run_tests.ProcessOutcome — exit_code and kind (EXIT, TIMEOUT, OS_ERROR), never returncode/cause.
    if outcome.kind == "TIMEOUT":
        return _row("execution:build-probe", EXECUTION, "WARN", f"фаза validate офлайн не уложилась в {PROBE_TIMEOUT} с — не проверено",
                    f"запустите вручную в {module_root}: {command}")
    if outcome.kind == "OS_ERROR":
        return _row("execution:build-probe", EXECUTION, "WARN", "фазу validate офлайн не удалось запустить — не проверено",
                    f"запустите вручную в {module_root}: {command}")
    if outcome.exit_code == 0:
        return _row("execution:build-probe", EXECUTION, "OK", f"фаза validate проекта офлайн прошла за {seconds} с (флаги exec)")
    output = f"{outcome.stdout}\n{outcome.stderr}"
    errors = [_scrub(line.strip()) for line in output.splitlines() if line.strip().startswith("[ERROR]") and "-> [Help" not in line][:3]
    if "offline mode" in output:
        return _row("execution:build-probe", EXECUTION, "WARN",
                    "фаза validate офлайн не проверена: плагинов или зависимостей проекта нет в локальном репозитории Maven; "
                    "первый exec пойдёт за ними в сеть — нужна сеть или зеркало компании",
                    "проверить заранее с сетью: " + command.replace(" -o ", " "))
    return _row("execution:build-probe", EXECUTION, "WARN",
                "фаза validate проекта падает с флагами exec — выбранные тесты не запустятся: " + (" | ".join(errors) or f"код {outcome.exit_code}"),
                f"исправьте сборку проекта, пока в {module_root} не пройдёт: {command}")
