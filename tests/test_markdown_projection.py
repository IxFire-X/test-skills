from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "canonical" / "valid" / "full-http.json"
GOLDEN = ROOT / "tests" / "fixtures" / "projections" / "full-http.markdown.bin"
sys.path.insert(0, str(ROOT))

from tools.canonical_document import CanonicalDocumentError  # noqa: E402
from tools.schema_validation import load_json_strict  # noqa: E402


EXPECTED_MARKDOWN = (
    "# Тест-кейсы метода GET /orders/{order\\_id}\n\n"
    "**Документация:** https://example.invalid/orders\n"
    "**Project:** EXAMPLE\n"
    "**Автор:** TEST\\_AUTHOR\n"
    "**Дата:** 2026-08-12\n\n"
    "---\n\n"
    "## ТК-1. Получение существующего заказа\n\n"
    "**Цель:** Проверить код ответа при получении заказа.\n\n"
    "**Предусловия:**\n"
    "- Заказ с идентификатором order-1 существует.\n\n"
    "**Шаги:**\n\n"
    "| № | Действие | Ожидаемый результат |\n"
    "|---|---|---|\n"
    "| 1 | Отправить GET /orders/{order\\_id} для существующего заказа. | Сервис возвращает HTTP 200. |\n"
).encode("utf-8")


class MarkdownProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = load_json_strict(FIXTURE)

    def render(self, document: dict):
        from tools.test_case_projections import render_markdown

        return render_markdown(document)

    def test_full_http_matches_independently_locked_byte_golden(self) -> None:
        """Changing Markdown layout or escaping changes independently authored bytes."""
        projection = self.render(self.document)
        self.assertEqual(EXPECTED_MARKDOWN, GOLDEN.read_bytes())
        self.assertEqual(EXPECTED_MARKDOWN, projection.payload)
        self.assertEqual((), projection.warnings)

    def test_http_metadata_case_step_order_and_multi_case_separator(self) -> None:
        """Projection must preserve physical arrays while computed labels remain one-based."""
        candidate = copy.deepcopy(self.document)
        first = candidate["test_cases"][0]
        first["preconditions"] = ["Первое.", "Второе."]
        first["steps"][0]["expectations"].append({
            "expectation_id": "EXP-order-extra", "display_order": 2,
            "text": "Второе ожидание.", "assertions": [{
                "assertion_id": "ASSERT-order-extra", "display_order": 1,
                "actual": {"kind": "http_status"}, "operator": "equals",
                "expected": {"kind": "literal", "value": 200},
            }],
        })
        second = copy.deepcopy(first)
        second["case_id"] = "TC-http-second"
        second["display_order"] = 2
        second["requirement_ids"] = ["REQ-http-example"]
        second["title"] = "Второй кейс"
        second["steps"][0]["step_id"] = "STEP-get-order-second"
        second["steps"][0]["expectations"] = [{
            "expectation_id": "EXP-order-second", "display_order": 1,
            "text": "Второе ожидание кейса.", "assertions": [{
                "assertion_id": "ASSERT-order-second", "display_order": 1,
                "actual": {"kind": "http_status"}, "operator": "equals",
                "expected": {"kind": "literal", "value": 200},
            }],
        }]
        candidate["test_cases"].append(second)

        payload = self.render(candidate).payload.decode("utf-8")
        self.assertIn("- Первое.\n- Второе.", payload)
        self.assertIn("Сервис возвращает HTTP 200.<br>Второе ожидание.", payload)
        self.assertIn("\n---\n\n## ТК-2. Второй кейс", payload)
        self.assertLess(payload.index("ТК-1"), payload.index("ТК-2"))

    def test_empty_documentation_and_preconditions_have_exact_human_defaults(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["metadata"]["documentation"] = []
        candidate["test_cases"][0]["preconditions"] = []
        projection = self.render(candidate)
        self.assertEqual(
            ('MISSING_DOCUMENTATION: metadata.documentation is empty; rendered as "Не предоставлена"',),
            projection.warnings,
        )
        self.assertIn("**Документация:** Не предоставлена", projection.payload.decode("utf-8"))
        self.assertIn("**Предусловия:**\n- Не требуются.", projection.payload.decode("utf-8"))

    def test_documentation_links_are_escaped_then_joined_by_generated_breaks(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["metadata"]["documentation"] = ["https://one|two\nthree", "<four>"]
        rendered = self.render(candidate).payload.decode("utf-8")
        self.assertIn(
            "**Документация:** https://one\\|two<br>three<br>\\<four\\>",
            rendered,
        )
        self.assertNotIn("\\<br\\>", rendered)

    def test_generic_subject_and_all_inline_escapes_are_exact(self) -> None:
        from tools.test_case_projections import escape_inline

        self.assertEqual(
            r"a\\b\`\*\_\[\]\<\>\#\|<br>c",
            escape_inline("a\\b`*_[]<>#|\r\nc"),
        )
        candidate = copy.deepcopy(self.document)
        candidate["metadata"]["subject"] = {"kind": "generic", "name": "A|B"}
        self.assertTrue(self.render(candidate).payload.startswith("# Тест-кейсы: A\\|B\n".encode("utf-8")))

    def test_markdown_has_no_bom_or_trailing_whitespace_and_never_mutates_input(self) -> None:
        before = copy.deepcopy(self.document)
        payload = self.render(self.document).payload
        self.assertEqual(before, self.document)
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(payload.endswith(b"\n"))
        self.assertFalse(payload.endswith(b"\n\n"))
        self.assertTrue(all(not line.endswith((b" ", b"\t")) for line in payload.splitlines()))

    def test_invalid_document_is_rejected_by_canonical_facade(self) -> None:
        with self.assertRaises(CanonicalDocumentError):
            self.render({})


if __name__ == "__main__":
    unittest.main()
