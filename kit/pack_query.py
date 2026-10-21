"""A read-only question of operate.py to the package, the package taken from <kit>/test-skills beside it.

    <package python> <kit>/pack_query.py modules <project>/.skillsrc      (cwd: <kit>/test-skills)

modules — the project's modules as the package's own .skillsrc parser sees them: a JSON list of {id, root, test} on
stdout; an unreadable .skillsrc — its error code and message on stderr, exit 1.  There is no other command; nothing is
written.
"""
import json
import sys
from pathlib import Path

# The package beside this script (as ts.py finds itself), never the working directory: a project's own tools/ must not
# stand in for the package's.
PACK = Path(__file__).resolve().parent / "test-skills"
sys.path.insert(0, str(PACK))

from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "modules":
        print("usage: pack_query.py modules <.skillsrc>", file=sys.stderr)
        return 2
    try:
        modules = normalize_skillsrc(load_skillsrc(Path(argv[1])))["modules"]
    except SkillsrcError as error:
        print(f"{error.code}: {error} {json.dumps(error.details, ensure_ascii=False)[:400]}", file=sys.stderr)
        return 1
    print(json.dumps([{"id": item["id"], "root": item.get("root", "."), "test": item.get("test") or {}} for item in modules], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
