"""Входы eval и замеров: снимки ревью step5 и Petclinic в том виде, в каком их строит драйвер."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = HERE / "data"
PETCLINIC_CASES = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
PETCLINIC_DOC = "docs/portable-owner-lifecycle-requirements.md"


def gz_json(name: str) -> Any:
    return json.loads(gzip.decompress((DATA / name).read_bytes()).decode("utf-8"))


def gz_text(name: str) -> str:
    return gzip.decompress((DATA / name).read_bytes()).decode("utf-8")


def _source(path: str, content: str) -> dict[str, str]:
    return {"path": path, "sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content}


def petclinic_document() -> dict[str, Any]:
    value = json.loads(PETCLINIC_CASES.read_text(encoding="utf-8"))
    return value.get("document", value)


def petclinic_case_snapshot(document: dict[str, Any] | None = None) -> dict[str, Any]:
    """Снимок ревью кейсов Petclinic: канон, исходные требования, без контекстов проекта."""
    document = petclinic_document() if document is None else document
    return {"document": copy.deepcopy(document), "automation": None, "package_binding": None,
            "sources": [_source(PETCLINIC_DOC, gz_text("petclinic-requirements.md.gz"))], "contexts": [],
            "requirements_binding": None}


_ANCHORED = re.compile(r"^\^|(?<!\\)\$$")


def petclinic_b7733d39_canonical() -> dict[str, Any]:
    """Канон прогона b7733d39 (сентябрь).

    Его regex-проверки записаны с ``^…$``, которые текущий валидатор portable-regex-v1
    отвергает (якоря подразумеваются).  Для ревью снимаются только эти символы; больше
    канон не меняется.
    """
    document = gz_json("petclinic-b7733d39-effective-canonical.json.gz")["document"]
    for case in document["test_cases"]:
        for step in case["steps"]:
            for expectation in step["expectations"]:
                for assertion in expectation["assertions"]:
                    expected = assertion.get("expected")
                    if isinstance(expected, dict) and expected.get("kind") == "regex":
                        expected["pattern"] = _ANCHORED.sub("", expected["pattern"])
    return document


def petclinic_automation_snapshot() -> dict[str, Any] | None:
    """Снимок ревью автотестов: сентябрьский класс b7733d39 (250 016 байт, 4288 строк) и его канон."""
    try:
        automation = gz_json("petclinic-b7733d39-automation-r1.json.gz")
    except FileNotFoundError:
        return None
    return {"document": petclinic_b7733d39_canonical(), "automation": automation, "package_binding": None,
            "sources": [_source(PETCLINIC_DOC, gz_text("petclinic-requirements.md.gz"))], "contexts": [],
            "requirements_binding": None}
