import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import validate_artifact

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "docs" / "to_do" / "skill-tests" / "tc-reviewer"
INPUTS = CAMPAIGN / "artifacts" / "inputs"
CHECKER = CAMPAIGN / "check_outputs.py"
CHECKER_V2 = CAMPAIGN / "check_outputs_v2.py"
CHECKER_V3 = CAMPAIGN / "check_outputs_v3.py"
INPUT_SCHEMA = ROOT / "schemas" / "tc-generator-output.schema.json"
OUTPUT_SCHEMA = ROOT / "schemas" / "tc-reviewer-output.schema.json"
SCENARIO = CAMPAIGN / "00-scenario.json"
PROTOCOL = CAMPAIGN / "PROTOCOL.md"
FIXTURES = (
    "clean-accepted",
    "typo-only",
    "blocking-missing-result",
    "blocking-fabricated-auth",
)
SAVED_GREEN_REP_01 = CAMPAIGN / "artifacts" / "outputs" / "02-green-initial" / "rep-01"


def _input_case(fixture):
    document = json.loads((INPUTS / f"{fixture}.json").read_text(encoding="utf-8"))
    return document["artifacts"]["generated_test_cases"]["test_cases"][0]


def _finding(severity, code, message, evidence, related_ids):
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "evidence": evidence,
        "related_ids": related_ids,
    }


def _output(verdict, case_id, findings=None, corrections=None, corrected=None):
    return {
        "schema_version": "2.1.0",
        "stage": "tc-reviewer",
        "artifacts": {
            "validation_report": {
                "verdict": verdict,
                "reviewed_test_case_ids": [case_id],
                "findings": findings or [],
                "corrections": corrections or [],
            },
            "corrected_test_cases": corrected or [],
        },
        "warnings": [],
    }


def _canonical_outputs():
    corrected_typo = copy.deepcopy(_input_case("typo-only"))
    corrected_typo["title"] = "Delete an owned active session"
    return {
        "clean-accepted": _output("ПРИНЯТО", "TC-CLEAN-001"),
        "typo-only": _output(
            "AUTO_FIX_APPLIED",
            "TC-TYPO-001",
            findings=[
                _finding(
                    "WARNING",
                    "TITLE_TYPO",
                    "The title spells session as sesion.",
                    ["TC-TYPO-001.title: sesion -> session"],
                    ["TC-TYPO-001"],
                )
            ],
            corrections=[
                {
                    "id": "FIX-TYPO-001",
                    "related_ids": ["TC-TYPO-001"],
                    "description": "Replace sesion with session in the title.",
                    "evidence": ["TC-TYPO-001.title: sesion -> session"],
                }
            ],
            corrected=[corrected_typo],
        ),
        "blocking-missing-result": _output(
            "ТРЕБУЕТ ДОРАБОТКИ",
            "TC-MISSING-001",
            findings=[
                _finding(
                    "BLOCKING",
                    "EXPECTED_RESULT_MISSING",
                    "The expected result is TBD, so the case is not executable.",
                    ["TC-MISSING-001.steps[0].expected_result=TBD", "TC-MISSING-001.expected_outcome=TBD"],
                    ["TC-MISSING-001", "REQ-CANCEL-001"],
                )
            ],
        ),
        "blocking-fabricated-auth": _output(
            "ТРЕБУЕТ ДОРАБОТКИ",
            "TC-AUTH-001",
            findings=[
                _finding(
                    "BLOCKING",
                    "UNSUPPORTED_AUTHORIZATION",
                    "warehouse_operator approval is unsupported; the requirement names only sales_manager.",
                    ["TC-AUTH-001 precondition: warehouse_operator", "REQ-AUTH-001 policy: sales_manager"],
                    ["TC-AUTH-001", "REQ-AUTH-001"],
                )
            ],
        ),
    }


def _write_outputs(output_dir, documents):
    output_dir.mkdir()
    for fixture, document in documents.items():
        (output_dir / f"{fixture}-tc-reviewer-output.json").write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def _copy_saved_green_outputs(output_dir):
    output_dir.mkdir()
    for fixture in FIXTURES:
        shutil.copy2(
            SAVED_GREEN_REP_01 / f"{fixture}-tc-reviewer-output.json",
            output_dir / f"{fixture}-tc-reviewer-output.json",
        )


def _run_checker(output_dir, mode="canonical", checker=CHECKER):
    return subprocess.run(
        [
            sys.executable,
            str(checker),
            "--input-dir",
            str(INPUTS),
            "--output-dir",
            str(output_dir),
            "--mode",
            mode,
        ],
        cwd=CAMPAIGN,
        text=True,
        capture_output=True,
        check=False,
    )


def test_campaign_inputs_are_distinct_and_schema_valid():
    payloads = []
    for fixture in FIXTURES:
        path = INPUTS / f"{fixture}.json"
        assert validate_artifact.validate(str(INPUT_SCHEMA), str(path))[0] == 0
        payloads.append(path.read_bytes())
    assert len(set(payloads)) == len(FIXTURES)


def test_red_v3_recovery_preserves_prompt_boundary_and_archives():
    """Catches recovery routes that change RED access, reuse evidence, or prefill output."""
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    red_phase = "01-red-control-v3"
    red_output = CAMPAIGN / "artifacts" / "outputs" / red_phase / "rep-01"
    red_protocol = CAMPAIGN / "artifacts" / "protocol" / red_phase / "rep-01"
    red_prompt = CAMPAIGN / "artifacts" / "prompts" / "01-red-control.txt"

    assert hashlib.sha256(red_prompt.read_bytes()).hexdigest() == (
        "6c4bc209686edcdb3f1907170e94cfa83c59740f2af2acc012fa43161b0350ac"
    )
    assert scenario["effective_red_repetitions"] == 1
    assert scenario["effective_red_phase"] == red_phase
    assert scenario["output_paths"][0] == f"artifacts/outputs/{red_phase}/rep-01"
    assert scenario["phase_prompt_sha256"][red_phase] == (
        "6c4bc209686edcdb3f1907170e94cfa83c59740f2af2acc012fa43161b0350ac"
    )
    assert red_output.joinpath(".gitkeep").read_bytes() == b"\n"
    assert red_protocol.joinpath(".gitkeep").read_bytes() == b"\n"
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in red_output.iterdir()} == {
        ".gitkeep": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
        "blocking-fabricated-auth-tc-reviewer-output.json": "766914339394f7db2f93310b423fdbc37487d8e7f8c8edefa8091f8ec0173b8b",
        "blocking-missing-result-tc-reviewer-output.json": "8533e481b28789c0657e6df887ee07a2b50a8091e987df114ba0517805fc6a44",
        "clean-accepted-tc-reviewer-output.json": "edd177ab4647dfb266c4309ebdc59705cff64ea95b9e80b2479955d00e00501c",
        "semantic-result.json": "c9cfa3a69701cdcf367712e48f1507a3b359e18bc07c243a67b7edeedcb60a37",
        "typo-only-tc-reviewer-output.json": "f337c0cbf9a7ee4c8d5351fae0bf2d2aabaf1e7d2ad55865b023387d7a21e88c",
        "validation-blocking-fabricated-auth.json": "2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01",
        "validation-blocking-missing-result.json": "2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01",
        "validation-clean-accepted.json": "2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01",
        "validation-typo-only.json": "2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01",
    }
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in red_protocol.iterdir()} == {
        ".gitkeep": "01ba4719c80b6fe911b091a7c05124b64eeece964e09c058ef8f9805daca546b",
        "observation.json": "5c81722b763130d33f3775b74d13e804e87128ce4f7475f841a87e1186b593fe",
        "prompt.txt": "6c4bc209686edcdb3f1907170e94cfa83c59740f2af2acc012fa43161b0350ac",
        "run-protocol.json": "3c2f4ff538c5fe3194778e330b9b16102e1df808610d98dff8f9526437375616",
    }

    assert scenario["required_skill_inputs"] == [
        {
            "path": "skills/tc-reviewer/SKILL.md",
            "sha256": "62fa4c00972cd5ab5a0deb6d9bafe045c13519b4a91da5f1d6d50fe5ff775a18",
        },
        {
            "path": "skills/tc-reviewer/references/review-verdicts.md",
            "sha256": "8d01417f6ce67f4b0badecaf624b60459522dbaf15aa42c03d9d2d32abc1ff78",
        },
        {
            "path": "schemas/tc-reviewer-output.schema.json",
            "sha256": "676035d151e2623308f007ff0b93026f0bb55bb3d1269a7a5ec7f6dee9d6e927",
        },
    ]
    protocol = PROTOCOL.read_text(encoding="utf-8")
    assert "`01-red-control-v3`" in protocol
    assert "only shared RED canonical input" in protocol
    assert "`skills/tc-reviewer/SKILL.md`" in protocol
    assert "`skills/tc-reviewer/references/review-verdicts.md`" in protocol
    assert "allOf" in protocol
    assert "no fixture-specific answers" in protocol
    assert "controller hash-verifies" in protocol
    assert "injects their exact ordered contents" in protocol

    assert (CAMPAIGN / "artifacts" / "outputs" / "01-red-control" / "rep-01" / ".gitkeep").is_file()
    assert (
        CAMPAIGN
        / "artifacts"
        / "outputs"
        / "01-red-control-v2"
        / "rep-01"
        / "clean-accepted-tc-reviewer-output.json"
    ).is_file()


def test_checker_accepts_canonical_outputs(tmp_path):
    output_dir = tmp_path / "outputs"
    documents = _canonical_outputs()
    _write_outputs(output_dir, documents)
    for fixture in FIXTURES:
        assert validate_artifact.validate(
            str(OUTPUT_SCHEMA), str(output_dir / f"{fixture}-tc-reviewer-output.json")
        )[0] == 0
    result = _run_checker(output_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "pass"


def test_checker_v2_accepts_typo_correction_with_case_and_requirement_links(tmp_path):
    documents = _canonical_outputs()
    documents["typo-only"]["artifacts"]["validation_report"]["corrections"][0]["related_ids"] = [
        "TC-TYPO-001",
        "REQ-SESSION-DELETE-001",
    ]
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir, checker=CHECKER_V2)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "pass"


@pytest.mark.parametrize(
    ("related_ids", "evidence", "expected_error"),
    [
        (
            ["REQ-SESSION-DELETE-001"],
            ["TC-TYPO-001.title: sesion -> session"],
            "correction must include TC-TYPO-001",
        ),
        (
            ["TC-TYPO-001", "REQ-DANGLING-999"],
            ["TC-TYPO-001.title: sesion -> session"],
            "dangling related_ids",
        ),
        (
            ["TC-TYPO-001"],
            ["TC-TYPO-001 session -> corrected"],
            "correction must bind title spelling evidence",
        ),
    ],
)
def test_checker_v2_separates_typo_case_link_from_known_ids_and_title_evidence(
    tmp_path, related_ids, evidence, expected_error
):
    documents = _canonical_outputs()
    correction = documents["typo-only"]["artifacts"]["validation_report"]["corrections"][0]
    correction["related_ids"] = related_ids
    correction["evidence"] = evidence
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir, checker=CHECKER_V2)

    assert result.returncode == 1
    assert any(expected_error in error for error in json.loads(result.stdout)["errors"])


@pytest.mark.parametrize(
    ("finding_ids", "correction_ids", "evidence", "exit_code"),
    [
        (["TC-TYPO-001"], ["TC-TYPO-001"], ["TC-TYPO-001.title: sesion -> session"], 0),
        (["TC-TYPO-001", "REQ-SESSION-DELETE-001"], ["TC-TYPO-001", "REQ-SESSION-DELETE-001"], ["TC-TYPO-001.title: sesion -> session"], 0),
        (["REQ-SESSION-DELETE-001"], ["TC-TYPO-001"], ["TC-TYPO-001.title: sesion -> session"], 1),
        (["TC-TYPO-001", "REQ-DANGLING-999"], ["TC-TYPO-001"], ["TC-TYPO-001.title: sesion -> session"], 1),
        (["TC-TYPO-001"], ["TC-TYPO-001"], ["TC-TYPO-001 session -> corrected"], 1),
    ],
)
def test_checker_v3_accepts_known_typo_finding_links_and_rejects_missing_or_ungrounded_links(
    tmp_path, finding_ids, correction_ids, evidence, exit_code
):
    documents = _canonical_outputs()
    review = documents["typo-only"]["artifacts"]["validation_report"]
    review["findings"][0]["related_ids"] = finding_ids
    review["findings"][0]["evidence"] = evidence
    review["corrections"][0]["related_ids"] = correction_ids
    review["corrections"][0]["evidence"] = evidence
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir, checker=CHECKER_V3)

    assert result.returncode == exit_code


def test_checker_accepts_saved_green_rep_01_outputs(tmp_path):
    output_dir = tmp_path / "outputs"
    _copy_saved_green_outputs(output_dir)

    result = _run_checker(output_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "pass"


@pytest.mark.parametrize(
    ("evidence", "expected_error"),
    [
        (["TC-TYPO-001 sesion -> session"], "title evidence"),
        (["TC-TYPO-001.title=session"], "title evidence"),
    ],
)
def test_checker_rejects_typo_evidence_without_title_or_original_typo(tmp_path, evidence, expected_error):
    documents = _canonical_outputs()
    typo_review = documents["typo-only"]["artifacts"]["validation_report"]
    typo_review["findings"][0]["evidence"] = evidence
    typo_review["corrections"][0]["evidence"] = evidence
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir)

    assert result.returncode == 1
    assert any(expected_error in error for error in json.loads(result.stdout)["errors"])


def test_checker_rejects_typo_mapping_without_corrected_spelling(tmp_path):
    documents = _canonical_outputs()
    typo_review = documents["typo-only"]["artifacts"]["validation_report"]
    typo_review["findings"][0]["message"] = "The title contains a spelling typo."
    typo_review["findings"][0]["evidence"] = ["TC-TYPO-001.title=Delete an owned active sesion"]
    typo_review["corrections"][0]["description"] = "Correct the title spelling."
    typo_review["corrections"][0]["evidence"] = ["TC-TYPO-001.title=Delete an owned active sesion"]
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir)

    assert result.returncode == 1
    assert any("sesion -> session" in error for error in json.loads(result.stdout)["errors"])


def test_checker_ignores_authorized_role_or_phrase(tmp_path):
    documents = _canonical_outputs()
    finding = documents["blocking-fabricated-auth"]["artifacts"]["validation_report"]["findings"][0]
    finding["message"] += " Provide an authorized role or a requirement-supported unauthorized outcome."
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "claim",
    [
        "A support_agent may approve instead.",
        "Use support_agent role instead.",
        "role: support_agent",
    ],
)
def test_checker_rejects_invented_roles_in_supported_claim_forms(tmp_path, claim):
    documents = _canonical_outputs()
    finding = documents["blocking-fabricated-auth"]["artifacts"]["validation_report"]["findings"][0]
    finding["message"] += f" {claim}"
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)

    result = _run_checker(output_dir)

    assert result.returncode == 1
    assert any("role tokens" in error for error in json.loads(result.stdout)["errors"])


@pytest.mark.parametrize(
    ("fixture", "mutation"),
    [
        ("clean-accepted", lambda document: document["artifacts"]["validation_report"].update({"verdict": "ТРЕБУЕТ ДОРАБОТКИ"})),
        ("typo-only", lambda document: document["artifacts"].update({"corrected_test_cases": []})),
        ("blocking-missing-result", lambda document: document["artifacts"]["validation_report"].update({"corrections": [{"id": "FIX-INVENTED-001", "related_ids": ["TC-MISSING-001"], "description": "Invent HTTP 201.", "evidence": ["No source evidence."]}]})),
        ("blocking-fabricated-auth", lambda document: document["artifacts"]["validation_report"].update({"verdict": "AUTO_FIX_APPLIED"})),
    ],
)
def test_checker_rejects_semantic_contract_mutations(tmp_path, fixture, mutation):
    documents = _canonical_outputs()
    mutation(documents[fixture])
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)
    result = _run_checker(output_dir)
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    assert any(fixture in error for error in payload["errors"])


def test_red_control_reports_semantic_gap_without_invalidating_execution(tmp_path):
    documents = _canonical_outputs()
    documents["clean-accepted"]["artifacts"]["validation_report"]["verdict"] = "ТРЕБУЕТ ДОРАБОТКИ"
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)
    result = _run_checker(output_dir, mode="red-control")
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "gap"


def test_checker_rejects_invented_status_and_role(tmp_path):
    documents = _canonical_outputs()
    missing = documents["blocking-missing-result"]["artifacts"]["validation_report"]["findings"][0]
    missing["message"] += " Use HTTP 201."
    auth = documents["blocking-fabricated-auth"]["artifacts"]["validation_report"]["findings"][0]
    auth["message"] += " A support_agent may approve instead."
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)
    result = _run_checker(output_dir)
    assert result.returncode == 1
    errors = json.loads(result.stdout)["errors"]
    assert any("HTTP statuses" in error for error in errors)
    assert any("role tokens" in error for error in errors)


@pytest.mark.parametrize(
    ("fixture", "mutate"),
    [
        (
            "blocking-missing-result",
            lambda finding: finding.update(code="UNSUPPORTED_AUTHORIZATION"),
        ),
        (
            "blocking-missing-result",
            lambda finding: finding.update(related_ids=["TC-MISSING-001"]),
        ),
        (
            "blocking-fabricated-auth",
            lambda finding: finding.update(code="EXPECTED_RESULT_MISSING"),
        ),
        (
            "blocking-fabricated-auth",
            lambda finding: finding.update(evidence=["warehouse_operator appears somewhere"]),
        ),
    ],
)
def test_checker_rejects_wrong_blocking_diagnosis_or_evidence(tmp_path, fixture, mutate):
    documents = _canonical_outputs()
    finding = documents[fixture]["artifacts"]["validation_report"]["findings"][0]
    mutate(finding)
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)
    result = _run_checker(output_dir)
    assert result.returncode == 1
    assert any(
        "semantic code, IDs, and field evidence" in error
        for error in json.loads(result.stdout)["errors"]
    )


def test_checker_accepts_alternative_valid_codes_ids_and_field_pointers(tmp_path):
    documents = _canonical_outputs()
    typo_review = documents["typo-only"]["artifacts"]["validation_report"]
    typo_review["findings"][0].update(
        code="SPELLING_ISSUE",
        evidence=["TC-TYPO-001.title contains sesion; replace with session"],
    )
    typo_review["corrections"][0].update(
        id="FIX-001",
        evidence=["TC-TYPO-001.title: sesion must become session"],
    )
    missing = documents["blocking-missing-result"]["artifacts"]["validation_report"]["findings"][0]
    missing.update(
        code="NON_EXECUTABLE_ORACLE",
        related_ids=["REQ-CANCEL-001", "TC-MISSING-001"],
        evidence=[
            "TC-MISSING-001.steps[0].expected_result has value TBD",
            "TC-MISSING-001.expected_outcome has value TBD",
        ],
    )
    auth = documents["blocking-fabricated-auth"]["artifacts"]["validation_report"]["findings"][0]
    auth.update(
        code="UNGROUNDED_ROLE_POLICY",
        related_ids=["REQ-AUTH-001", "TC-AUTH-001"],
        evidence=[
            "TC-AUTH-001.preconditions[0] names warehouse_operator",
            "REQ-AUTH-001 names sales_manager",
        ],
    )
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)
    result = _run_checker(output_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "pass"


def test_checker_uses_exit_2_for_unusable_output(tmp_path):
    documents = _canonical_outputs()
    output_dir = tmp_path / "outputs"
    _write_outputs(output_dir, documents)
    (output_dir / "clean-accepted-tc-reviewer-output.json").write_text("{", encoding="utf-8")
    result = _run_checker(output_dir)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "error"


def test_red_v3_controller_preflight_is_separate_from_repetition_commands():
    campaign = ROOT / "docs" / "to_do" / "skill-tests" / "tc-reviewer"
    protocol = json.loads(
        (
            campaign
            / "artifacts"
            / "protocol"
            / "01-red-control-v3"
            / "rep-01"
            / "run-protocol.json"
        ).read_text(encoding="utf-8")
    )
    preflight = protocol["controller_preflight"]
    assert preflight == {
        "id": "role-integrity-check",
        "argv": [
            r"C:\Program Files\Git\bin\sh.exe",
            "C:/Users/User/.codex/plugins/cache/sol-advisor/sol-advisor/0.5.0/scripts/install-agents.sh",
            "--check",
        ],
        "cwd": str(ROOT),
        "exit_code": 0,
    }
    assert [command["id"] for command in protocol["commands"]] == [
        "validate-artifact-clean-accepted",
        "validate-artifact-typo-only",
        "validate-artifact-blocking-missing-result",
        "validate-artifact-blocking-fabricated-auth",
        "semantic-check",
    ]
    assert all(command["cwd"] == str(campaign) for command in protocol["commands"])


def test_initial_green_v3_protocol_invalid_archive_preserves_the_v4_rep04_prefix():
    """A v3 invalidation cannot consume the completed v4/rep-03..04 prefix."""
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    metadata = json.loads((CAMPAIGN / "06-run-metadata.json").read_text(encoding="utf-8"))
    archive_root = (
        CAMPAIGN
        / "artifacts"
        / "invalidated"
        / "02-green-initial-v3"
        / "rep-03"
        / "green-initial-v3-rep-03-ambient-bootstrap-read"
    )

    assert scenario["green_initial_phase_by_repetition"] == {
        "rep-01": "02-green-initial-v2",
        "rep-02": "02-green-initial-v2",
        "rep-03": "02-green-initial-v4",
        "rep-04": "02-green-initial-v4",
        "rep-05": "02-green-initial-v5",
    }
    assert "artifacts/outputs/02-green-initial-v3/rep-03" not in scenario["output_paths"]
    for repetition in ("rep-03", "rep-04"):
        run = next(
            item
            for item in metadata["runs"]
            if (item["phase"], item["repetition"]) == ("02-green-initial-v4", repetition)
        )
        assert len(run["outputs"]) == 10
        assert (CAMPAIGN / "02-green-initial-v4" / f"{repetition}.md").read_text(encoding="utf-8").startswith(
            f"# GREEN initial v4 — {repetition}\n\n`02-green-initial-v4/{repetition}` is a successful active run."
        )
    attempt = next(
        item
        for item in metadata["invalidated_attempts"]
        if item["attempt_id"] == "green-initial-v3-rep-03-ambient-bootstrap-read"
    )
    assert (attempt["phase"], attempt["repetition"], attempt["excluded_from_score"]) == (
        "02-green-initial-v3",
        "rep-03",
        True,
    )
    assert attempt["commands"] == [
        {
            "id": "ambient-bootstrap-skill-read",
            "declared_target": "superpowers:using-superpowers",
            "argv": None,
            "cwd": None,
            "exit_code": None,
            "diagnostic": (
                "Native trace reported one system skill-instruction read but did not expose "
                "literal argv, cwd, or exit code."
            ),
        }
    ]
    for output in attempt["outputs"]:
        archived = CAMPAIGN / output["path"]
        reserved = (
            CAMPAIGN
            / "artifacts"
            / "outputs"
            / "02-green-initial-v3"
            / "rep-03"
            / archived.name
        )
        assert archived.read_bytes() == reserved.read_bytes()
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == output["sha256"]
    protocol = json.loads((archive_root / "run-protocol.json").read_text(encoding="utf-8"))
    assert protocol["commands"] == attempt["commands"]
    assert protocol["controller_preflight"]["attempts"] == [
        {"execution_context": "sandbox", "error": "CreateProcessAsUserW failed: 5"},
        {"execution_context": "escalated", "exit_code": 0, "output": "CHECK PASSED"},
    ]


def test_v4_rep05_checker_false_negative_is_archived_and_v5_rep05_is_successful():
    """The frozen v1 false-negative remains archived while v5 closes the active prefix."""
    scenario = json.loads(SCENARIO.read_text(encoding="utf-8"))
    metadata = json.loads((CAMPAIGN / "06-run-metadata.json").read_text(encoding="utf-8"))
    archive_root = (
        CAMPAIGN
        / "artifacts"
        / "invalidated"
        / "02-green-initial-v4"
        / "rep-05"
        / "green-initial-v4-rep-05-semantic-checker-false-negative"
    )
    active_root = CAMPAIGN / "artifacts" / "outputs" / "02-green-initial-v4" / "rep-05"

    assert scenario["green_initial_phase_by_repetition"]["rep-05"] == "02-green-initial-v5"
    assert ("02-green-initial-v5", "rep-05") in {
        (run["phase"], run["repetition"]) for run in metadata["runs"]
    }
    assert ("02-green-initial-v4", "rep-05") not in {
        (run["phase"], run["repetition"]) for run in metadata["runs"]
    }
    v5_run = next(
        run for run in metadata["runs"]
        if (run["phase"], run["repetition"]) == ("02-green-initial-v5", "rep-05")
    )
    assert [command["id"] for command in v5_run["commands"]] == [
        "ambient-bootstrap-skill-read",
        "validate-artifact-clean-accepted",
        "validate-artifact-typo-only",
        "validate-artifact-blocking-missing-result",
        "validate-artifact-blocking-fabricated-auth",
        "semantic-check",
    ]
    assert v5_run["commands"][-1]["argv"][1].endswith("check_outputs_v2.py")
    assert "successful active run" in (
        CAMPAIGN / "02-green-initial-v5" / "rep-05.md"
    ).read_text(encoding="utf-8")

    attempt = next(
        item
        for item in metadata["invalidated_attempts"]
        if item["attempt_id"] == "green-initial-v4-rep-05-semantic-checker-false-negative"
    )
    assert attempt["reason_codes"] == [
        "semantic-check-failed",
        "semantic-checker-false-negative",
    ]
    assert attempt["excluded_from_score"] is True
    assert [command["id"] for command in attempt["commands"]] == [
        "ambient-bootstrap-skill-read",
        "validate-artifact-clean-accepted",
        "validate-artifact-typo-only",
        "validate-artifact-blocking-missing-result",
        "validate-artifact-blocking-fabricated-auth",
        "semantic-check",
    ]
    assert attempt["commands"][-1]["exit_code"] == 1
    assert attempt["semantic_result"] == {
        "status": "fail",
        "exit_code": 1,
        "errors": ["typo-only: correction must bind the same title spelling evidence"],
    }
    assert attempt["checker"] == {
        "path": "check_outputs.py",
        "sha256": "8757973e2cea5d479ee83e4e19a7a290f142de052624d04857aace828736e4ec",
    }
    assert attempt["replacement_checker"] == {
        "path": "check_outputs_v2.py",
        "sha256": "7b39077be85a115d52ab7ad8d6f4cb4e11a09053dffe09a19e81708b42dc971b",
    }
    assert len(attempt["outputs"]) == 9
    assert archive_root.joinpath("prompt.txt").read_bytes() == (
        CAMPAIGN / "artifacts" / "protocol" / "02-green-initial-v4" / "rep-05" / "prompt.txt"
    ).read_bytes()
    for output in attempt["outputs"]:
        archived = CAMPAIGN / output["path"]
        active = active_root / archived.name
        assert archived.read_bytes() == active.read_bytes()
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == output["sha256"]
    protocol = json.loads((archive_root / "run-protocol.json").read_text(encoding="utf-8"))
    observation = json.loads((archive_root / "observation.json").read_text(encoding="utf-8"))
    assert protocol["commands"] == observation["repetition_commands"] == attempt["commands"]
    assert protocol["controller_preflight"] == observation["controller_preflight"]


def test_reviewer_rejects_an_oracle_that_the_declared_harness_cannot_produce():
    skill = " ".join((ROOT / "skills" / "tc-reviewer" / "SKILL.md").read_text(encoding="utf-8").split())
    contract = " ".join(
        (ROOT / "skills" / "tc-reviewer" / "references" / "review-verdicts.md")
        .read_text(encoding="utf-8")
        .split()
    )

    for required in (
        "setup → action → observable result",
        "the declared harness can actually produce",
        "response media type, body shape, status, and state",
        "blocking",
    ):
        assert required in skill
    assert "HARNESS_ORACLE_MISMATCH" in contract
