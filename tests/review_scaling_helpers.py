"""Shared inputs of the review-scaling tests: real step5/Petclinic snapshots and synthetic documents."""
from __future__ import annotations

import copy
import hashlib
import random
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "evals" / "review-scaling"
if str(EVAL) not in sys.path:
    sys.path.insert(0, str(EVAL))

import eval_data  # noqa: E402

from tests.live_step5 import review_state  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "review-scaling"


def step5_snapshot(run: str = "9340016c") -> dict[str, Any]:
    """The recorded canonical review snapshot payload of a live step5 run."""
    return copy.deepcopy(review_state(run, "review-snapshot-canonical")["payload"])


def petclinic_snapshot() -> dict[str, Any]:
    return eval_data.petclinic_case_snapshot()


def _sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def synthetic_case(case_id: str, size: int, requirement: str, order: int) -> dict[str, Any]:
    stem = case_id.removeprefix("TC-")
    return {
        "case_id": case_id, "display_order": order, "title": f"Синтетический кейс {stem}", "objective": "О" * max(1, size),
        "priority": "HIGH", "categories": ["positive"], "requirement_ids": [requirement], "preconditions": ["Приложение запущено."],
        "management": {"components": [], "custom_fields": {}, "estimated_time": None, "external_keys": {}, "external_links": {"issues": [], "pages": []},
                       "folder": None, "labels": [], "owner": None, "status": None},
        "steps": [{
            "step_id": f"STEP-{stem}-01", "display_order": 1, "action": "Вызвать операцию.", "test_data": "Данные шага.",
            "operation": {"kind": "project_action", "capability_id": "CAP-S-call"}, "manual_only": False, "manual_reason": None,
            "automation_blockers": [], "outputs": [],
            "inputs": [{"input_id": f"INPUT-{stem}-01-1", "display_order": 1, "target": {"location": "arg", "name": "path", "sensitive": False},
                        "source": {"kind": "literal", "value": f"/items/{stem}"}, "semantic_type": {"kind": "json", "type": "string"}}],
            "expectations": [{"expectation_id": f"EXP-{stem}-01-1", "display_order": 1, "text": "Возвращается 200.",
                              "assertions": [{"assertion_id": f"ASSERT-{stem}-01-1-1", "display_order": 1, "actual": {"kind": "project_result", "name": "status"},
                                              "operator": "equals", "expected": {"kind": "literal", "value": 200}}]}],
        }],
    }


def synthetic_document(sizes: list[int], *, seed: int = 0, ids: list[str] | None = None) -> dict[str, Any]:
    rng = random.Random(seed)
    ids = ids or [f"TC-S{rng.getrandbits(32):08x}" for _ in sizes]
    requirements = [f"CREQ-S-{index}" for index in range(7)]
    return {
        "schema_version": "1.0.0", "content_locale": "ru-RU", "document_id": "TCDOC-synthetic", "revision": 1, "parent_sha256": None,
        "metadata": {"subject": {"kind": "generic", "name": "Синтетика"}, "documentation": ["docs/req.md"], "project": "synthetic",
                     "author": "test", "date": "2026-10-07"},
        "operation_capabilities": [{"capability_id": "CAP-S-call", "adapter": "project_native_junit", "action": "call",
                                    "arguments": [{"name": "path", "required": True, "semantic_type": {"kind": "json", "type": "string"}}],
                                    "results": [{"name": "status", "semantic_type": {"kind": "json", "type": "integer"}}],
                                    "provenance": ["Синтетическая возможность."]}],
        "source_requirements": [{"source_requirement_id": "SREQ-S-1", "display_order": 1, "text": "Требование.", "provenance": ["docs/req.md#L1-L1"],
                                 "digest": _sha("Требование.")}],
        "requirements": [{"requirement_id": item, "display_order": index + 1, "text": f"Нормализованное требование {index}.", "provenance": ["SREQ-S-1"]}
                         for index, item in enumerate(requirements)],
        "source_to_canonical_mappings": [{"source_requirement_id": "SREQ-S-1", "canonical_requirement_ids": requirements}],
        "test_cases": [synthetic_case(case_id, size, requirements[index % len(requirements)], index + 1) for index, (case_id, size) in enumerate(zip(ids, sizes))],
    }


def synthetic_snapshot(document: dict[str, Any]) -> dict[str, Any]:
    content = "# Требования\nТребование.\n"
    return {"document": document, "automation": None, "package_binding": None, "contexts": [], "requirements_binding": None,
            "sources": [{"path": "docs/req.md", "sha256": _sha(content), "content": content}]}
