"""S2d (security review note 8): Python dependencies install only by hash (`pip install --require-hashes`)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)((?:\s+--hash=sha256:[0-9a-f]{64})+)$")


def _requirements(name: str) -> dict[str, tuple[str, list[str]]]:
    text = (ROOT / name).read_text(encoding="utf-8").replace("\\\n", " ")
    rows = {}
    for line in text.splitlines():
        line = " ".join(line.split("#", 1)[0].split())
        if not line or line.startswith("-r "):
            continue
        match = _REQUIREMENT.fullmatch(line)
        assert match, f"{name}: not a pinned requirement with hashes: {line}"
        rows[match.group(1).lower().replace("_", "-")] = (match.group(2), re.findall(r"[0-9a-f]{64}", match.group(3)))
    return rows


def test_every_runtime_and_dev_requirement_is_pinned_by_hash() -> None:
    runtime, dev = _requirements("requirements.txt"), _requirements("requirements-dev.txt")
    # The whole closure: --require-hashes refuses an unlisted dependency (referencing needs typing-extensions before 3.13).
    assert set(runtime) == {"jsonschema", "pyyaml", "jsonschema-specifications", "referencing", "rpds-py", "attrs", "typing-extensions"}
    assert set(dev) == {"pytest", "iniconfig", "pluggy", "packaging", "pygments", "colorama"}
    # Compiled packages: wheels for CPython 3.11–3.13 on Windows x64, Linux x86_64 and aarch64, plus the sdist.
    assert len(runtime["rpds-py"][1]) == 10 and len(runtime["pyyaml"][1]) == 10
    assert all(len(hashes) == 2 for name, (_version, hashes) in {**runtime, **dev}.items() if name not in {"rpds-py", "pyyaml"})
    assert "-r requirements.txt" in (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")


def test_the_pins_agree_with_the_release_freeze() -> None:
    frozen = {}
    for line in (ROOT / "release" / "dependencies.lock.txt").read_text(encoding="utf-8").splitlines():
        if "==" in line and not line.startswith("#"):
            name, version = line.strip().split("==")
            frozen[name.lower().replace("_", "-")] = version
    for name, (version, _hashes) in {**_requirements("requirements.txt"), **_requirements("requirements-dev.txt")}.items():
        assert frozen.get(name) == version, name


def test_every_install_instruction_requires_hashes() -> None:
    for name in ("QUICKSTART.md", "USAGE.md", "README.md", ".github/workflows/portable.yml"):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if "pip install" in line and "-r " in line:
                assert "--require-hashes" in line, f"{name}: {line.strip()}"
