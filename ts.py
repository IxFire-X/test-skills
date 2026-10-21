"""test-skills launcher: ``<python> <pack>/ts.py next|submit|status|answer|doctor ...`` from any directory.

The orchestrating CLI session starts every command this way, with the interpreter the kit's ``init`` used (the venv
with jsonschema) and without ``cd``: the command a person confirms in the CLI then begins with that interpreter.
The pack directory goes first on ``sys.path``; ``next``, ``submit``, ``status`` and ``answer`` run
``tools.pipeline_driver``, ``doctor`` runs ``tools.doctor`` for this pack.  There is no other command.
"""

import json
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parent
DRIVER_COMMANDS = ("next", "submit", "status", "answer")


def main(argv: list[str]) -> int:
    command = argv[0] if argv else None
    if command in DRIVER_COMMANDS or command == "doctor":
        sys.path.insert(0, str(PACK))
    if command in DRIVER_COMMANDS:
        from tools.pipeline_driver import main as driver

        return driver(argv)
    if command == "doctor":
        from tools.doctor import main as doctor

        rest = argv[1:]
        sys.argv = ["ts.py doctor", *(rest if "--root" in rest else ["--root", str(PACK), *rest])]
        return doctor()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"action": "error", "code": "LAUNCHER_COMMAND",
                      "message": f"ts.py takes one of: {', '.join((*DRIVER_COMMANDS, 'doctor'))}"}, ensure_ascii=False, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
