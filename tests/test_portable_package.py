import json
import re
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

ARTIFACT_ALIAS_RE = re.compile(r"^(generated_files|trace_map|automation_bundle_json)$")
SKILL_DECL_RE = re.compile(r"^\s*(accepts|forwards|output)\s*:")
ROOT_ARTIFACT_DECL_RE = re.compile(r"^(?:\||[-*]\s+).*\b(artifact|accepts|forwards|output)\b", re.IGNORECASE)
INLINE_LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
REFERENCE_LINK_RE = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE)
FENCED_CODE_RE = re.compile(r"(?:^|\n)(?:```|~~~).*?(?:\n```|\n~~~)", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
CANONICAL_SKILLS = (
    "context-marker",
    "tc-generator",
    "tc-reviewer",
    "tc-to-autotest",
    "autotest-reviewer",
    "orchestrate",
)
OLD_DIRECTORIES = (
    "Разметка контекста",
    "Ручные тест-кейсы",
    "Валидация тест-кейсов",
    "Автоматизированные кейсы на основе тест-кейсов",
    "Валидация автотестов",
    "Оркестратор",
)
ROOT_DOCS = ("CONTRACTS.md", "Instruction.md", "PIPELINE.md", "ROADMAP.md", "USER-GUIDE.md")


def _markdown_sources(root):
    sources = [root / name for name in ROOT_DOCS]
    sources.extend((root / "docs").rglob("*.md"))
    sources.extend((root / "skills").rglob("*.md"))
    if (root / "adapters").exists():
        sources.extend((root / "adapters").rglob("*.md"))
    for path in sorted(set(sources)):
        relative = path.relative_to(root)
        parts = relative.parts
        if parts[:3] == ("docs", "to_do", "real-chains"):
            continue
        if "artifacts" in parts and parts[:3] == ("docs", "to_do", "skill-tests"):
            continue
        if "archive" in parts or "skill-snapshot" in parts:
            continue
        if any(part in {"node_modules", ".venv", "venv", "target", "build", "dist", "__pycache__"} for part in parts):
            continue
        yield path


def _link_target(raw):
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        raw = raw[1 : raw.index(">")]
    else:
        raw = raw.split(maxsplit=1)[0]
    if not raw or raw.startswith("#"):
        return None
    parsed = urlsplit(raw)
    if parsed.scheme or raw.startswith("//"):
        return None
    return unquote(parsed.path)


def test_authority_boundaries_use_only_canonical_names(root):
    pipeline = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    declared_artifacts = {item["id"] for item in pipeline["artifacts"]}
    for step in pipeline["steps"]:
        declared_artifacts.update(step["accepts"])
        declared_artifacts.update(step["forwards"])
        declared_artifacts.update(step["produces"])
    assert not {name for name in declared_artifacts if ARTIFACT_ALIAS_RE.fullmatch(name)}

    authority_lines = []
    for path in (root / "skills").rglob("*.md"):
        authority_lines.extend(line for line in path.read_text(encoding="utf-8").splitlines() if SKILL_DECL_RE.match(line))
    for name in ROOT_DOCS:
        authority_lines.extend(
            line
            for line in (root / name).read_text(encoding="utf-8").splitlines()
            if ROOT_ARTIFACT_DECL_RE.match(line)
        )
    for line in authority_lines:
        assert not any(ARTIFACT_ALIAS_RE.fullmatch(token) for token in re.findall(r"[A-Za-z_]+", line))
        assert not any(old in line for old in OLD_DIRECTORIES)

    plan = (root / "docs" / "superpowers" / "plans" / "2026-08-06-portable-skills-adapters.md").read_text(
        encoding="utf-8"
    )
    expected_migrations = {
        f'git mv "$pack\\{old}" "$pack\\skills\\{skill}"'
        for old, skill in zip(OLD_DIRECTORIES, CANONICAL_SKILLS, strict=True)
    }
    actual_migrations = {line.strip() for line in plan.splitlines() if line.strip().startswith('git mv "$pack\\')}
    assert actual_migrations == expected_migrations


def test_local_markdown_links_resolve(root):
    broken = []
    for source in _markdown_sources(root):
        text = source.read_text(encoding="utf-8")
        text = INLINE_CODE_RE.sub("", FENCED_CODE_RE.sub("", text))
        raw_targets = INLINE_LINK_RE.findall(text) + REFERENCE_LINK_RE.findall(text)
        for raw_target in raw_targets:
            target = _link_target(raw_target)
            if target is None:
                continue
            resolved = (source.parent / target).resolve()
            if not resolved.exists():
                broken.append((source.relative_to(root).as_posix(), raw_target))
    assert broken == []


def test_portable_skill_packages_and_campaign_evidence_are_complete(root):
    pipeline = json.loads((root / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    assert tuple(pipeline["skill_files"]) == CANONICAL_SKILLS
    assert {path.name for path in (root / "skills").iterdir() if path.is_dir()} == set(CANONICAL_SKILLS)

    allowed_entries = {"SKILL.md", "references", "scripts", "assets"}
    for skill, relative_path in pipeline["skill_files"].items():
        package = root / "skills" / skill
        assert root / relative_path == package / "SKILL.md"
        assert (package / "SKILL.md").is_file()
        assert {path.name for path in package.iterdir()} <= allowed_entries
        assert not (package / "README.md").exists()
        assert not (package / "SKILL-LITE.md").exists()
        assert not (package / "templates").exists()

    evidence_schema = json.loads((root / "schemas" / "skill-test-evidence.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(evidence_schema)
    for skill in CANONICAL_SKILLS:
        campaign = root / "docs" / "to_do" / "skill-tests" / skill
        evidence_paths = [
            campaign / "00-scenario.json",
            campaign / "06-run-metadata.json",
            campaign / "05-scorecards" / "red.json",
            campaign / "05-scorecards" / "green-initial.json",
            campaign / "05-scorecards" / "green-final.json",
        ]
        for path in evidence_paths:
            assert path.is_file(), path
            artifact = json.loads(path.read_text(encoding="utf-8"))
            assert list(validator.iter_errors(artifact)) == [], path
