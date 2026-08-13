"""Internal, optional launcher for the JDK-only loopback reference adapter."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "reference_adapters" / "java" / "SharedContractReference.java"
REGEX_CORPUS = ROOT / "tests" / "fixtures" / "portable-regex-v1" / "cases.json"
HTTP_CORPUS_ROOT = ROOT / "tests" / "fixtures" / "http-binding-v1"
EXPECTED_CASES = [
    {"case_id": "echo", "status": "PASS"},
    {"case_id": "redirect", "status": "PASS"},
    {"case_id": "retryable", "status": "PASS"},
    {"case_id": "gzip", "status": "PASS"},
    {"case_id": "close", "status": "PASS"},
]
EXPECTED_CORPORA = [
    {"count": 64, "corpus": "portable-regex-v1", "status": "PASS"},
    {"count": 23, "corpus": "assertion-v1", "status": "PASS"},
    {"count": 131, "corpus": "http-request-v1", "status": "PASS"},
    {"count": 47, "corpus": "http-response-v1", "status": "PASS"},
    {"count": 40, "corpus": "http-phase-v1", "status": "PASS"},
]
_LOOPBACK_ORIGIN = re.compile(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})\Z")


def _report(status: str, **fields: Any) -> dict[str, Any]:
    return {"adapter": "java", "status": status, **fields}


def _emit(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _origin(value: str | None) -> bool:
    if not isinstance(value, str):
        return False
    match = _LOOPBACK_ORIGIN.fullmatch(value)
    return match is not None and 1 <= int(match.group(1)) <= 65535


def _failure_exit(require_integration: bool) -> int:
    return 1 if require_integration else 0


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicates)


def _fixture_contract_is_exact() -> bool:
    try:
        portable = _load_json(REGEX_CORPUS)
        if set(portable) != {"portable_regex_v1", "assertion_v1"}:
            return False
        if set(portable["portable_regex_v1"]) != {"cases"} or set(portable["assertion_v1"]) != {"cases"}:
            return False
        actual = [
            {"count": len(portable["portable_regex_v1"]["cases"]), "corpus": "portable-regex-v1", "status": "PASS"},
            {"count": len(portable["assertion_v1"]["cases"]), "corpus": "assertion-v1", "status": "PASS"},
        ]
        for filename, kind, corpus in (
            ("request-cases.json", "request", "http-request-v1"),
            ("response-cases.json", "response", "http-response-v1"),
            ("phase-cases.json", "phase", "http-phase-v1"),
        ):
            value = _load_json(HTTP_CORPUS_ROOT / filename)
            required = {"contract", "corpus_version", "kind", "cases"}
            if set(value) - {"value_encodings"} != required:
                return False
            if value["contract"] != "http-binding-v1" or value["corpus_version"] != "1.0.0" or value["kind"] != kind:
                return False
            case_ids = [row.get("case_id") for row in value["cases"]]
            if any(not isinstance(case_id, str) for case_id in case_ids) or len(case_ids) != len(set(case_ids)):
                return False
            actual.append({"count": len(value["cases"]), "corpus": corpus, "status": "PASS"})
        return actual == EXPECTED_CORPORA
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        return False


def _single_json_report(stdout: str) -> dict[str, Any] | None:
    lines = stdout.splitlines()
    if len(lines) != 1:
        return None
    try:
        result = json.loads(lines[0], object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _pass_report_is_exact(result: dict[str, Any]) -> bool:
    return (
        set(result) == {"adapter", "cases", "corpora", "protocol_headers", "status"}
        and result.get("adapter") == "java"
        and result.get("status") == "PASS"
        and result.get("cases") == EXPECTED_CASES
        and result.get("corpora") == EXPECTED_CORPORA
        and result.get("protocol_headers") == ["content-length"]
    )


def run(origin: str | None, require_integration: bool) -> tuple[dict[str, Any], int]:
    javac, java = shutil.which("javac"), shutil.which("java")
    if javac is None:
        return _report("SKIPPED", reason="javac_unavailable"), 1 if require_integration else 0
    if java is None:
        return _report("SKIPPED", reason="java_unavailable"), 1 if require_integration else 0
    if origin is None:
        return _report("SKIPPED", reason="origin_required"), 1 if require_integration else 0
    if not _origin(origin):
        return _report("FAIL", code="invalid_origin"), _failure_exit(require_integration)
    if not _fixture_contract_is_exact():
        return _report("FAIL", code="corpus_contract_mismatch"), _failure_exit(require_integration)
    with tempfile.TemporaryDirectory(prefix="test-skills-java-") as directory:
        compile_result = subprocess.run(
            [javac, "--release", "11", "-d", directory, str(SOURCE)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if compile_result.returncode:
            return _report("FAIL", code="javac_compile_failed"), _failure_exit(require_integration)
        process = subprocess.run(
            [
                java,
                "-Djdk.httpclient.disableRetryConnect=true",
                "-Djdk.httpclient.enableAllMethodRetry=false",
                "-Djava.net.useSystemProxies=false",
                "-cp",
                directory,
                "SharedContractReference",
                "--origin",
                origin,
                "--corpus-root",
                str(ROOT),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    parsed = _single_json_report(process.stdout)
    if parsed is None:
        return _report("FAIL", code="java_invalid_report"), _failure_exit(require_integration)
    if process.returncode or parsed.get("status") != "PASS":
        return _report("FAIL", code="java_contract_failed"), _failure_exit(require_integration)
    if not _pass_report_is_exact(parsed):
        return _report("FAIL", code="java_invalid_report"), _failure_exit(require_integration)
    return parsed, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin")
    parser.add_argument("--require-integration", action="store_true")
    args = parser.parse_args(argv)
    try:
        result, code = run(args.origin, args.require_integration)
    except subprocess.TimeoutExpired:
        result, code = _report("FAIL", code="java_timeout"), _failure_exit(args.require_integration)
    _emit(result)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
