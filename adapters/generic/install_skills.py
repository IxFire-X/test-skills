#!/usr/bin/env python3
"""Install the six canonical skill packages without changing their bytes."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

CANONICAL_SKILLS = (
    "context-marker",
    "tc-generator",
    "tc-reviewer",
    "tc-to-autotest",
    "autotest-reviewer",
    "orchestrate",
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Directory containing canonical skill folders.")
    parser.add_argument("--destination", required=True, type=Path, help="Destination that will contain skill folders.")
    parser.add_argument("--dry-run", action="store_true", help="Report the copy plan without creating the destination.")
    return parser.parse_args()


def _is_transient(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store"


def _source_files(source: Path) -> list[Path]:
    files: list[Path] = []
    for skill in CANONICAL_SKILLS:
        package = source / skill
        if not package.is_dir() or not (package / "SKILL.md").is_file():
            raise ValueError(f"missing canonical skill package: {skill}")
        files.extend(path for path in package.rglob("*") if path.is_file() and not _is_transient(path))
    return sorted(files, key=lambda path: path.relative_to(source).as_posix())


def _same_file(source: Path, destination: Path) -> bool:
    if not destination.is_file():
        return False
    source_stat = source.stat()
    destination_stat = destination.stat()
    if source_stat.st_size != destination_stat.st_size or source_stat.st_mtime_ns != destination_stat.st_mtime_ns:
        return False
    return source.read_bytes() == destination.read_bytes()


def _validate_paths(source: Path, destination: Path) -> tuple[Path, Path]:
    source = source.resolve()
    destination = destination.resolve()
    if not source.is_dir():
        raise ValueError(f"source directory does not exist: {source}")
    if destination == source or source in destination.parents:
        raise ValueError("destination must not be inside the source skills directory")
    return source, destination


def main() -> int:
    args = _parse_args()
    try:
        source, destination = _validate_paths(args.source, args.destination)
        source_files = _source_files(source)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2

    copied = 0
    unchanged = 0
    if not args.dry_run:
        for source_file in source_files:
            target = destination / source_file.relative_to(source)
            if _same_file(source_file, target):
                unchanged += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target)
            copied += 1

    print(
        json.dumps(
            {
                "status": "planned" if args.dry_run else "installed",
                "source": str(source),
                "destination": str(destination),
                "files": len(source_files),
                "copied": copied,
                "unchanged": unchanged,
                "dry_run": args.dry_run,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
