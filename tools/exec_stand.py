"""P8a-1: the stand check — the one place of the package that contacts the application under test (FaT-2R).

Moved out of ``run_pipeline`` unchanged; a delivery without this file cannot open a connection.
"""

from __future__ import annotations

from pathlib import Path

from tools import pilot_state


SERVICE_TIMEOUT_SECONDS = 3


def _safe_address(address: str) -> tuple[str, bool]:
    """The stand's address to show — scheme, host, port and path; userinfo, query and fragment (where secrets live) cut —
    and whether its port is valid (an out-of-range or non-numeric port is left out of the shown address)."""
    import urllib.parse

    parts = urllib.parse.urlsplit(address)
    host = parts.hostname or ""
    try:
        port, valid = parts.port, True
    except ValueError:
        port, valid = None, False
    netloc = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, "", "")), valid


def _service_stop(project: Path, module_root: str) -> dict[str, str] | None:
    """FaT-2R: for a module with ``service_url_env`` — one request without a body to the stand's address (any HTTP
    answer counts), no wait and no retry: the agent decides.  ``{code, reason, message, fix, address}`` when the
    variable is missing, not an http(s) address or nobody answers; the address without userinfo, query and fragment."""
    import os
    import urllib.error
    import urllib.parse
    import urllib.request

    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc

    try:
        modules = normalize_skillsrc(load_skillsrc(project / ".skillsrc"))["modules"]
    except (OSError, SkillsrcError, ValueError, KeyError, TypeError):
        return None
    wanted = module_root.strip("/") or "."
    module = next((row for row in modules if (str(row.get("root") or ".").strip("/") or ".") == wanted), None)
    variable = str((module or {}).get("service_url_env") or "")
    if not variable:
        return None
    fix = f"поднимите стенд приложения по адресу из переменной {variable} и запустите exec снова"
    address = os.environ.get(variable, "")

    def stop(code: str, message: str, shown: str | None) -> dict[str, str]:
        return {"code": code, "reason": code, "message": message, "fix": fix, "service_url_env": variable,
                **({"address": shown} if shown else {})}

    if not address:
        return stop("SERVICE_URL_MISSING", f"переменная {variable} с адресом стенда не задана", None)
    try:
        scheme = urllib.parse.urlsplit(address).scheme
        shown, port_valid = _safe_address(address)
    except ValueError:
        scheme, shown, port_valid = "", None, False
    if scheme not in {"http", "https"}:
        return stop("SERVICE_URL_INVALID", f"в {variable} не http(s)-адрес", None)
    if not port_valid:
        return stop("SERVICE_URL_INVALID", f"в {variable} неверный порт (не число или вне 0–65535)", shown)
    try:
        # no proxy: the Java tests do not read HTTP_PROXY, and a corporate proxy answering 502/403 is not the stand
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(address, method="HEAD"), timeout=SERVICE_TIMEOUT_SECONDS):
            pass
    except urllib.error.HTTPError:
        return None  # the stand answered: an HTTP error status is an answer
    except (urllib.error.URLError, OSError, ValueError) as error:
        return stop("SERVICE_UNREACHABLE", f"стенд {shown} (из {variable}): {_unreachable_reason(error)}; "
                    "тесты не запускались, файлы не записаны", shown)
    return None


def _unreachable_reason(error: BaseException) -> str:
    """FaT-SU: why the stand gave no answer — a closed port, an unknown host, a timeout or TLS — from the one attempt."""
    import socket
    import ssl
    import urllib.error

    cause = error.reason if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException) else error
    if isinstance(cause, (socket.timeout, TimeoutError)) or "timed out" in str(cause):
        return f"не ответил за {SERVICE_TIMEOUT_SECONDS} с"
    if isinstance(cause, ssl.SSLError):
        return "ошибка TLS"
    if isinstance(cause, socket.gaierror):
        return "хост не найден"
    if isinstance(cause, ConnectionRefusedError):
        return "никто не слушает порт"
    return f"соединение не удалось ({type(cause).__name__})"


def stand_down(run_root: Path, attempt_id: str) -> bool:
    """FaT-O12 O2: the module's stand (FaT-2R ``service_url_env``) does not answer now — the same check as exec's."""
    attempt = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    project = Path(str(pilot_state.read_run(run_root)["manifest"]["project"]))
    return _service_stop(project, str(attempt["module"])) is not None
