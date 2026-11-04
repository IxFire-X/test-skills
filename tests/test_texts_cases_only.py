"""PT: the texts of the cases-only delivery say what the delivery has — and nothing about what it has not.

README, HOW-TO-USE and the files of ``skills/`` that the profile carries describe cases without a project run: no automated tests, no
build, no stand.  Mode B (``local-pilot-v1``) is described in ``skills/orchestrate/references/mode-b.md`` of the development build only.
Every command the texts name exists in the cases-only pack, with the flags the text gives."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pack_profile import copy_profile

ROOT = Path(__file__).resolve().parents[1]
OLD_SKILL_BYTES = 61838  # skills/orchestrate/SKILL.md before PT: the agent at work reads it whole into a window of 239k tokens
ALLOWED = (
    "Автотесты (`local-pilot-v1`, режим Б) — только в сборке разработки, в эту поставку они не входят.",
    "Режим автотестов (режим Б) в эту поставку не входит.",
)
MODE_B_WORDS = [
    r"local-pilot", r"rerun-retained", r"run_pipeline exec", r"service_url_env", r"SERVICE_URL", r"SERVICE_UNREACHABLE", r"стенд", r"maven",
    r"gradle", r"\bmvn\b", r"pytest", r"карантин", r"@Disabled", r"accepted=true", r"tc-to-autotest", r"autotest-reviewer", r"mode-b",
    r"generated-source", r"project-native generated", r"reviewed test source",
]
SECURITY_WORDS = [r"безопасн", r"AppSec", r"Amber", r"Green", r"Red flag", r"🟢", r"🟡", r"🔴", r"вердикт безопасности"]


def _profile_texts() -> dict[str, str]:
    from tools import pack_registry

    paths = pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], "cases-only")
    names = ["README.md", "HOW-TO-USE.md", *(path for path in paths if path.startswith("skills/") and path.endswith(".md"))]
    return {name: (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n") for name in names}


def _flat(text: str) -> str:
    flat = " ".join(text.split())
    for sentence in ALLOWED:
        flat = flat.replace(" ".join(sentence.split()), "")
    return flat


def test_the_delivered_texts_do_not_describe_mode_b() -> None:
    offenders = []
    for name, text in _profile_texts().items():
        flat = _flat(text)
        offenders += [f"{name}: {word}" for word in MODE_B_WORDS if re.search(word, flat, re.IGNORECASE)]
    assert offenders == []


def test_readme_and_how_to_use_say_nothing_about_a_security_review() -> None:
    texts = _profile_texts()
    offenders = [f"{name}: {word}" for name in ("README.md", "HOW-TO-USE.md") for word in SECURITY_WORDS if re.search(word, texts[name], re.IGNORECASE)]
    assert offenders == []


def test_the_one_sentence_about_mode_b_is_in_the_skill_and_the_readme() -> None:
    texts = _profile_texts()
    assert " ".join(ALLOWED[0].split()) in " ".join(texts["skills/orchestrate/SKILL.md"].split())
    assert " ".join(ALLOWED[1].split()) in " ".join(texts["README.md"].split())
    assert "mode-b" not in " ".join(texts["skills/orchestrate/SKILL.md"].split()), "the delivery names no file it does not carry"


def test_the_controller_skill_is_not_longer_than_before_and_the_mode_b_text_is_kept_for_the_development_build() -> None:
    assert len((ROOT / "skills" / "orchestrate" / "SKILL.md").read_bytes()) < OLD_SKILL_BYTES
    mode_b = (ROOT / "skills" / "orchestrate" / "references" / "mode-b.md").read_text(encoding="utf-8")
    for needle in ("local-pilot-v1", "run_pipeline exec", "tc-to-autotest", "карантин"):
        assert needle in mode_b


def test_mode_b_text_and_its_roles_are_in_the_registry_and_not_in_the_cases_only_profile() -> None:
    from tools import pack_registry

    paths = [row["path"] for row in pack_registry.registry_rows(ROOT)]
    left = {"skills/orchestrate/references/mode-b.md", "skills/tc-to-autotest/SKILL.md", "skills/autotest-reviewer/SKILL.md",
            "skills/tc-to-autotest/references/automation-output-contract.md", "skills/autotest-reviewer/references/autotest-review-contract.md"}
    assert left <= set(paths)
    kept = set(pack_registry.profile_paths(paths, "cases-only"))
    assert not left & kept
    assert not [path for path in kept if path.startswith(("skills/tc-to-autotest/", "skills/autotest-reviewer/"))]
    assert {"skills/orchestrate/SKILL.md", "skills/orchestrate/references/fragment-sample.md", "skills/tc-generator/SKILL.md"} <= kept


ANCHORS = (
    "`context_receipt`", "целиком", "--prior-fragment", "`path` из вывода её `model-response`", "validate-fragment", "--context-receipt <f> --run-root <run>`",
    "fragment-sample.md", "schemas/candidate-fragment.schema.json", "<run>/published", "OUTPUT_DIR_OUTSIDE_PROJECT",
    "read-receipt --run-root <run> --attempt <id> --kind reviewer-session-boundary --event ARTIFACT_READ_BACK", "read-effective", "read-ledger",
    "review-evidence", "`for_human`", "REVIEW_CANARY_ONLY_VERDICT", "REVIEW_ONLY_CANARY_FIXES", "REVIEW_CANARY_MISMATCH", "REVIEW_UNRELIABLE",
    "details.self_review_scan", "`SCOPE_PENDING`", "reviewer-answer.json",
)


@pytest.mark.parametrize("anchor", ANCHORS)
def test_the_skill_keeps_what_the_agent_at_petclinic_stumbled_on(anchor: str) -> None:
    text = " ".join((ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8").split())
    assert anchor in text


def test_how_to_use_covers_the_pause_the_section_the_fresh_session_and_the_weak_review_mark() -> None:
    text = " ".join((ROOT / "HOW-TO-USE.md").read_text(encoding="utf-8").split())
    for needle in ("Пауза после разметки", "`SCOPE_PENDING`", "--docs файл.md#Раздел", "Ревью в свежей сессии", "ревью слабое: пропущено N из M",
                   "ревью ненадёжно (`REVIEW_UNRELIABLE`)", "published/", "плейсхолдером `<…>`"):
        assert needle in text, needle
    assert "ссылкой на переменную" not in text


def test_the_readme_has_the_line_about_the_skill_header() -> None:
    text = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "Если ваш CLI ругается на шапку файлов `SKILL.md`" in text and "напишите разработчикам" in text


# ---- every command of the texts is a command of the cases-only pack ----------------------------------------------------

_COMMAND = re.compile(r"tools\.([a-z_]+)(?:\s+([a-z][a-z-]*))?")


def _span_commands(text: str) -> set[tuple[str, str | None, tuple[str, ...]]]:
    """``(module, subcommand, flags)`` of every one-line code span or line of a fenced block that names a ``tools.`` command."""
    found = set()
    spans = [" ".join(span.split()) for span in re.findall(r"`([^`]+)`", text)] + [line for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S) for line in block.split("\n")]
    for span in spans:
        match = _COMMAND.search(span)
        if not match:
            continue
        module, sub = match.group(1), match.group(2)
        flags = tuple(dict.fromkeys(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", span[match.end():])))
        found.add((module, sub if module in {"api", "run_pipeline"} else None, flags))
    return found


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("texts") / "pack", "cases-only")


def _help(pack: Path, module: str, sub: str | None) -> tuple[int, str]:
    done = subprocess.run([sys.executable, "-m", f"tools.{module}", *([sub] if sub else []), "--help"], cwd=pack, capture_output=True, text=True,
                          encoding="utf-8", env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return done.returncode, done.stdout + done.stderr


def test_every_command_the_texts_name_exists_in_the_cases_only_pack_with_the_flags_the_text_gives(cases_only_pack: Path) -> None:
    texts = {name: text for name, text in _profile_texts().items()
             if name in {"README.md", "HOW-TO-USE.md", "skills/orchestrate/SKILL.md", "skills/orchestrate/references/orchestration-contract.md",
                         "skills/orchestrate/references/fragment-sample.md"}}
    problems, seen = [], set()
    for name, text in texts.items():
        for module, sub, flags in sorted(_span_commands(text), key=repr):
            if not (cases_only_pack / "tools" / f"{module}.py").is_file():
                problems.append(f"{name}: tools.{module} is not in the cases-only pack")
                continue
            if module not in {"api", "run_pipeline"} and sub is None:
                continue
            code, output = _help(cases_only_pack, module, sub) if (module, sub) not in seen else (0, "")
            seen.add((module, sub))
            if code != 0:
                problems.append(f"{name}: tools.{module} {sub} has no --help ({code})")
                continue
            if output:
                missing = [flag for flag in flags if not re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", output)]
                if missing:
                    problems.append(f"{name}: tools.{module} {sub} has no {missing}")
    assert problems == []
    assert ("api", "validate-fragment") in seen and ("run_pipeline", "finish") in seen and ("api", "review-open") in seen


def test_the_execution_commands_are_not_in_the_cases_only_pack(cases_only_pack: Path) -> None:
    for sub in ("exec", "rerun-retained"):
        code, output = _help(cases_only_pack, "run_pipeline", sub)
        assert code != 0, sub


def test_the_json_blocks_of_the_fragment_sample_are_the_form_of_the_schemas(tmp_path: Path) -> None:
    """The sample is checked against the schemas it points to: the case, a requirement, a mapping and a capability, placeholders filled."""
    import shutil

    from tools.schema_validation import schema_diagnostics

    text = (ROOT / "skills" / "orchestrate" / "references" / "fragment-sample.md").read_text(encoding="utf-8")
    fragment, case, *_forms = (json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S))  # PT2: then the forms of the plan inputs
    schema = json.loads((ROOT / "schemas" / "candidate-fragment.schema.json").read_text(encoding="utf-8"))
    assert sorted(set(schema["required"]) - set(fragment)) == [] and sorted(set(fragment) - set(schema["properties"])) == []
    tokens = {"<ns>": "B1", "<SREQ-… этой пачки>": "SREQ-0001", "<SREQ-…>": "SREQ-0001", "<имя>": "batch_ids", "<имя операции>": "batch_ids",
              "<адаптер из входов>": "project_native_junit"}

    def fill(value):
        if isinstance(value, str):
            for token, replacement in tokens.items():
                value = value.replace(token, replacement)
            return "текст" if re.fullmatch(r"<[^>]*>", value) else value
        if isinstance(value, list):
            return [fill(item) for item in value]
        if isinstance(value, dict):
            return {key: fill(item) for key, item in value.items()}
        return value

    copy = tmp_path / "schemas"
    shutil.copytree(ROOT / "schemas", copy)
    for name, definition, value in (("case", "test_case", case), ("requirement", "requirement", fragment["requirements"][0]),
                                    ("mapping", "source_to_canonical_mapping", fragment["source_to_canonical_mappings"][0]),
                                    ("capability", "capability", fragment["operation_capabilities"][0])):
        target = copy / f"sample-{name}.schema.json"
        target.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"schemas/sample-{name}.schema.json",
                                      "$ref": f"canonical-test-document.schema.json#/$defs/{definition}"}), encoding="utf-8")
        assert schema_diagnostics(fill(value), target, tmp_path) == [], name
