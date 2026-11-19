"""Report portable skill-pack integrity: the files of the pack, their SHA-256, line endings said in words.

A small checker.  It reads ``release/manifest.json`` and compares the pack with it (``tools.pack_registry``): the exact set of
registry files of one profile, the bytes of each, the manifest's own digest and schema.  It writes nothing, starts nothing, and
leaves no mark: no other tool reads its answer back as a fact — every run checks again.
"""

import json
import platform
import sys
from pathlib import Path

if __package__:
    from .json_cli import JsonArgumentParser
else:  # direct CLI execution
    from json_cli import JsonArgumentParser


CRLF_FIX = ("положите в папку пакета файл .gitattributes со строкой `* -text` (git перестанет менять переносы строк пакета) и верните "
            "файлы пакета: git checkout -- <папка пакета> или заново скопируйте поставку")
MANIFEST_FIX = "сверьте папку пакета с поставкой (файлы и SHA-256): скопируйте поставку заново"


def _registry():
    if __package__:
        from . import pack_registry
    else:  # direct CLI execution
        import pack_registry
    return pack_registry


def _engine_importable() -> bool:
    """The package's own schema validator (tools/schema_engine.py, standard library only) imports and validates."""
    try:
        if __package__:
            from .schema_engine import Draft202012Validator
        else:  # direct CLI execution
            from schema_engine import Draft202012Validator
        return Draft202012Validator({"type": "string"}).is_valid("x")
    except Exception:
        return False


def inspect_environment(root: Path) -> dict[str, object]:
    """Return the integrity of a skill pack and what it can run."""
    root = root.resolve()
    registry = _registry()
    qualification: dict[str, object] = {"state": "unavailable", "ready_tuple": None}
    missing_integrity: list[str] = []
    messages: list[str] = []
    if not root.is_dir():
        missing_integrity.append("root")
    dependencies = {"schema_engine": {"required": True, "available": _engine_importable()}}
    missing_modules = [name for name, item in dependencies.items() if not item["available"]]
    # without it the manifest cannot be checked — say what is missing, not "invalid manifest".
    messages.extend(
        f"не импортируется собственный валидатор схем ({name}, tools/schema_engine.py); сверка с манифестом пропущена. "
        "Сверьте папку пакета с поставкой: стороннее ПО пакету не нужно"
        for name in missing_modules
    )
    profile: str | None = None
    crlf_report: dict[str, object] | None = None
    if root.is_dir() and not (root / "release" / "manifest.json").is_file():
        missing_integrity.append("release/manifest.json")
        messages.append(f"нет release/manifest.json: пакет нечем сверить; {MANIFEST_FIX}")
    elif root.is_dir() and not missing_modules:
        try:
            profile, manifest = registry.verify_pack(root)
            qualification = dict(manifest["qualification"])
        except (ImportError, TypeError, ValueError) as error:
            missing_integrity.append("invalid:release/manifest.json")
            messages.append(f"release/manifest.json: {type(error).__name__}: {error}")
            crlf = registry.crlf_converted(root) if "does not match runtime bytes" in str(error) else []
            if crlf:  # said in words; the byte-for-byte check is unchanged
                crlf_report = {"count": len(crlf), "examples": crlf[:5], "fix": CRLF_FIX}
                messages.append(f"переносы строк изменены (CRLF) в {len(crlf)} файл(ах) пакета, например {', '.join(crlf[:3])}: "
                                f"содержимое то же, но git при checkout заменил LF на CRLF. {CRLF_FIX}")
            if "match no delivery profile" in str(error):
                try:
                    _nearest, extra, absent = registry.profile_diff(root, registry.load_manifest(root))
                except ValueError:
                    extra, absent = [], []
                for path in extra[:10]:
                    if "/__pycache__/" in path and path.endswith(".pyc"):
                        messages.append(f"кэш байт-кода {path} не совпадает с исходником (или исходника нет): удалите каталог {path.rsplit('/', 1)[0]}")
                    else:
                        messages.append(f"лишний файл {path}: удалите его (в пакете такого файла нет)")
                messages.extend(f"нет файла {path}: скопируйте поставку заново" for path in absent[:10])
    python_supported = sys.version_info >= (3, 11)
    ready = not missing_integrity and python_supported and all(item["available"] for item in dependencies.values())
    runs_projects = profile != "cases-only"  # the cases-only pack has no module that starts a build or a test
    return {
        "status": "PASS" if ready else "NOT_RUNNABLE",
        "status_scope": "pack_integrity",
        "profile": profile,
        "qualification": qualification,
        "python": {
            "supported": python_supported,
            "version": platform.python_version(),
        },
        "dependencies": dependencies,
        "messages": messages,
        "integrity": {"valid": not missing_integrity, "missing": missing_integrity},
        **({"crlf": crlf_report} if crlf_report else {}),
        # PT2: what the pack can run is said only by a pack that can run it; the delivery of cases has nothing to say here
        **({"languages": {
            "java": {"execution": True, "verified": False},
            "python": {"execution": True, "verified": False},
            "typescript": {"execution": False, "verified": False},
            "go": {"execution": False, "verified": False},
        }, "adapters": {
            "local_project_native": {"core": True, "verified": False},
            "company_runner": {"core": False, "verified": False},
        }} if runs_projects else {}),
        "root": str(root),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(
        description="Report portable skill-pack runtime readiness as JSON."
    )
    parser.add_argument("--root", type=Path, required=True, help="Path to the skill pack")
    args = parser.parse_args()
    report = inspect_environment(args.root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
