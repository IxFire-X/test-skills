"""S11 of the security fixes (handoff/plan-security-s11.md, re-review of 54eb8b9).

A1: requirement documents reach the reviewers masked like everywhere else; a secret-named file is no requirement
document.  The digest of a source stays the digest of the original file (it binds the frozen requirements); a masked
source carries its redactions and the journal re-reads the original to check the mask.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tools import pipeline_driver as driver

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def _with_secret_document(tmp_path: Path) -> Path:
    project = _project(tmp_path, local=False)
    document = project / "docs" / "feature.md"
    document.write_text(document.read_text(encoding="utf-8") + f"\nПример настройки: aws_access_key_id = {AWS_KEY}\naws_secret_access_key = {AWS_SECRET}\n",
                        encoding="utf-8")
    return project


def test_the_reviewers_get_the_requirement_documents_masked(tmp_path: Path) -> None:
    from tools.pilot_state import read_attempt_receipt

    project = _with_secret_document(tmp_path)
    model = SavedModel("cases-only-v1")
    seen: list[dict] = []
    done = _drive(project, _start(project, "cases-only-v1"), model, until=lambda task: seen.append(task) or False)
    assert done["action"] == "done", done
    reviews = [task for task in seen if str(task.get("stage", "")).startswith("tc-reviewer:")]
    assert reviews
    for task in reviews:
        text = "".join(Path(path).read_text(encoding="utf-8", errors="replace") for path in task["inputs"])
        assert AWS_KEY not in text and AWS_SECRET not in text, task["stage"]
    run_root = _run_root(project, done)
    snapshot = read_attempt_receipt(run_root, done["result"]["attempt_id"], "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"]
    [source] = snapshot["sources"]
    assert "[REDACTED:" in source["content"] and source["redactions"]
    original = (project / "docs" / "feature.md").read_bytes()
    assert source["sha256"] == "sha256:" + __import__("hashlib").sha256(original).hexdigest()  # the digest of the original file


def test_a_clean_document_keeps_the_source_format_of_the_records(tmp_path: Path) -> None:
    """No secret, no change: the replayed Petclinic records keep their snapshots byte for byte."""
    payload = driver._review_payload(*_payload_args(tmp_path), package=None, automation=None)
    assert all(set(source) == {"path", "sha256", "content"} for source in payload["sources"])


def _payload_args(tmp_path: Path):
    project = _project(tmp_path, local=False)
    task = _drive(project, _start(project, "cases-only-v1"), SavedModel("cases-only-v1"), until=lambda task: str(task.get("stage", "")).startswith("tc-reviewer:"))
    run_root = _run_root(project, task)
    attempt = driver._attempt(run_root)
    config = {**driver._config(run_root), "review_mode": "pairs"}
    document = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8")).get("document") or {"test_cases": []}
    return project, run_root, attempt, config, document


def _masked_source(project: Path) -> dict:
    from tools.project_inventory import redact_text

    raw = (project / "docs" / "feature.md").read_bytes()
    content, redactions = redact_text(raw.decode("utf-8"))
    digest = lambda data: "sha256:" + __import__("hashlib").sha256(data).hexdigest()  # noqa: E731
    return {"path": "docs/feature.md", "sha256": digest(raw), "content": content, "content_sha256": digest(content.encode("utf-8")), "redactions": redactions}


def test_a_forged_mask_is_refused_and_a_later_spec_edit_does_not_break_the_journal(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.pilot_state import read_attempt_receipt

    project = _with_secret_document(tmp_path)
    source = _masked_source(project)
    pilot_state._check_review_source(project, project, source, publishing=True)
    forged_text = {**source, "content": source["content"] + "приписано\n"}
    with pytest.raises(ValueError):  # the masked text no longer matches its digest: refused on read too
        pilot_state._check_review_source(project, project, forged_text)
    forged_mask = {**source, "redactions": [{**source["redactions"][0], "line": source["redactions"][0].get("line", 0) + 1}]}
    with pytest.raises(ValueError):  # redactions that are not the mask of the original: refused when written
        pilot_state._check_review_source(project, project, forged_mask, publishing=True)

    # A spec edited after the run: the journal stays self-contained.
    model = SavedModel("cases-only-v1")
    done = _drive(project, _start(project, "cases-only-v1"), model)
    run_root = _run_root(project, done)
    (project / "docs" / "feature.md").write_text("# новая спека\n", encoding="utf-8")
    pilot_state._VALIDATED_REVIEW_RECEIPTS.clear()
    snapshot = read_attempt_receipt(run_root, done["result"]["attempt_id"], "review-snapshot-canonical", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert snapshot["sources"][0]["redactions"]
    assert driver.status(project, run_root)["attempt_state"] == "TERMINAL"


@pytest.mark.parametrize("name", [".env", ".env.local", "server.pem", "deploy.key", ".netrc", "id_rsa"])
def test_a_secret_named_file_is_no_requirement_document(tmp_path: Path, name: str) -> None:
    project = _project(tmp_path, local=False)
    (project / name).write_text("# требования\nREQ-1 что-то\n", encoding="utf-8")
    done = driver.start_run(project, {"profile": "cases-only-v1", "docs": [name], "subject": "x", "model_id": "m"})
    assert done["action"] == "done" and done["result"]["reason"] == "SNAPSHOT_DOCS", done


# --------------------------------------------------------------------------------------
# A3: the environment of the processes the package starts
# --------------------------------------------------------------------------------------

def test_the_suite_runner_gets_the_build_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from tests.test_offline_build import MAVEN, _executable, _host
    from tools import suite_run

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "x" * 36)
    monkeypatch.setenv("MAVEN_OPTS", "-Xmx512m")
    _executable(tmp_path / _host("mvnw", "mvnw.cmd"))
    seen = []
    monkeypatch.setattr(suite_run, "RUNNER", lambda argv, **kwargs: seen.append(kwargs) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    module = {"id": "root", "root": ".", "test": {"framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw", "build_profile": "default", "adapter_parameters": {}}}
    suite_run.run_once(tmp_path, module, ["pkg.ATest#a"], explicit=False, timeout=60)
    assert "GITHUB_TOKEN" not in seen[0]["env"] and seen[0]["env"]["MAVEN_OPTS"] == "-Xmx512m"


def test_a_secret_name_is_removed_even_when_a_build_name_would_keep_it() -> None:
    from tools.child_environment import build_environment, removal_reasons

    source = {"GRADLE_PUBLISH_TOKEN": "t", "TMP_TOKEN": "t", "GRADLE_OPTS": "-Xmx1g", "TMPDIR": "/tmp", "PATH": "/bin", "HTTPS_PROXY": "http://proxy:3128",
              "HTTP_PROXY": "http://user:pa55@proxy:3128", "no_proxy": "localhost"}
    env = build_environment(source)
    assert set(env) == {"GRADLE_OPTS", "TMPDIR", "PATH", "HTTPS_PROXY", "no_proxy"}
    reasons = removal_reasons(source)
    assert reasons == {"GRADLE_PUBLISH_TOKEN": "secret-name", "TMP_TOKEN": "secret-name", "HTTP_PROXY": "proxy-credentials"}
    assert "pa55" not in json.dumps(reasons)


@pytest.mark.parametrize("names", [["MY_API_TOKEN"], ["ANTHROPIC_API_KEY"], ["AWS_*"], ["GIGA_SECRET*"], ["AZURE_CLIENT"], ["G*"], ["AB*"],
                                   ["GOOGLE_APPLICATION_CREDENTIALS"], ["SERVICE_PASSWORD"]])
def test_env_passthrough_never_names_a_credential(names: list[str]) -> None:
    from tools import model_runner

    with pytest.raises(model_runner.RunnerError) as raised:
        model_runner.configure({"review_runner": {"preset": "claude", "env_passthrough": names}})
    assert "конфиг" in str(raised.value)


def test_env_passthrough_keeps_proxies_and_cli_settings() -> None:
    from tools import model_runner

    config = model_runner.configure({"review_runner": {"preset": "claude", "env_passthrough": ["HTTPS_PROXY", "GIGACODE_*", "NO_PROXY"]}})
    assert config.env_passthrough == ("HTTPS_PROXY", "GIGACODE_*", "NO_PROXY")


# --------------------------------------------------------------------------------------
# A4: the manual scan turns the human gates on like the driver
# --------------------------------------------------------------------------------------

def _manual_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **flags) -> tuple[Path, str, dict]:
    from types import SimpleNamespace

    from tests.test_phase2_scan import _project as _python_project
    from tools import pilot_state, run_pipeline

    project, _module = _python_project(tmp_path)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    assert run_pipeline.cmd_scan(SimpleNamespace(project=str(project), module="nested", target=None, docs=None, **flags)) == 0
    run_root = Path(payloads[-1]["run_root"])
    _manifest, authorization = pilot_state._run_boundary(project.resolve(), run_root)
    return run_root, payloads[-1]["attempt_id"], authorization


@pytest.mark.human_gates
def test_a_manual_scan_without_flags_needs_the_approval_before_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    run_root, attempt_id, authorization = _manual_scan(tmp_path, monkeypatch)
    assert authorization.get("execution_review_required") is True and authorization.get("context_confirm_required") is True, authorization
    with pytest.raises(ValueError):  # what exec claims first: no build or test run without EXECUTION_APPROVED
        pilot_state.claim_execution_start(run_root, attempt_id, "sha256:" + "0" * 64)


@pytest.mark.human_gates
def test_the_scan_flags_turn_the_gates_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_root, _attempt, authorization = _manual_scan(tmp_path, monkeypatch, execution_review="none", confirm_context="no")
    assert "execution_review_required" not in authorization and "context_confirm_required" not in authorization, authorization


# --------------------------------------------------------------------------------------
# A5: the suite never removes a report directory through a link
# --------------------------------------------------------------------------------------

def _link(link: Path, target: Path) -> None:
    import os

    if os.name == "nt":
        from tests.helpers import make_junction

        make_junction(link, target)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("where", ["module-root", "report-parent"])
def test_the_suite_refuses_a_linked_module_or_report_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str) -> None:
    from tests.test_offline_build import MAVEN, _executable, _host
    from tools import execution_adapters as adapters, suite_run

    project, outside = tmp_path / "project", tmp_path / "outside"
    keep = outside / "target" / "surefire-reports" / "keep.xml" if where == "module-root" else outside / "surefire-reports" / "keep.xml"
    keep.parent.mkdir(parents=True)
    keep.write_text("<keep/>", encoding="utf-8")
    project.mkdir()
    if where == "module-root":
        _link(project / "api", outside)
        root = "api"
    else:
        _link(project / "target", outside)
        root = "."
    _executable(project / root / _host("mvnw", "mvnw.cmd")) if where == "report-parent" else _executable(outside / _host("mvnw", "mvnw.cmd"))
    monkeypatch.setattr(suite_run, "RUNNER", lambda *_args, **_kwargs: pytest.fail("the suite started a process through a linked directory"))
    module = {"id": "root", "root": root, "test": {"framework": "junit5", "adapter_id": MAVEN, "wrapper": "mvnw", "build_profile": "default", "adapter_parameters": {}}}
    with pytest.raises(adapters.AdapterRequestError):
        suite_run.run_once(project, module, ["pkg.ATest#a"], explicit=False, timeout=60)
    assert keep.is_file()  # the directory outside the project is untouched
