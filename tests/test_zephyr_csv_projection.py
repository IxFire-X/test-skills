from __future__ import annotations

import copy
import csv
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "canonical" / "valid" / "full-http.json"
GOLDEN = ROOT / "tests" / "fixtures" / "projections" / "full-http.zephyr-scale.csv.bin"
sys.path.insert(0, str(ROOT))

from tools.canonical_document import CanonicalDocumentError, require_valid_canonical_document  # noqa: E402
from tools.schema_validation import load_json_strict  # noqa: E402


HEADERS = [
    "Key", "Name", "Status", "Precondition", "Objective", "Folder", "Priority",
    "Component", "Labels", "Owner", "Estimated Time", "Coverage (Issues)",
    "Coverage (Pages)", "АС", "Автоматизирован", "Вид тестирования", "Команда",
    "Приоритет теста", "Статус", "Test Script (Step-by-Step) - Step",
    "Test Script (Step-by-Step) - Test Data",
    "Test Script (Step-by-Step) - Expected Result", "Test Script (Plain Text)",
    "Test Script (BDD)",
]


def projection_document() -> dict:
    """A test-local, schema+semantic-valid enriched copy of full-http.json."""
    document = copy.deepcopy(load_json_strict(FIXTURE))
    case = document["test_cases"][0]
    case["preconditions"] = ["Первое условие.", "Второе условие."]
    case["management"] = {
        "status": "Open", "folder": "Orders", "components": ["orders", "api"],
        "labels": ["http", "smoke"], "owner": "owner", "estimated_time": "5m",
        "external_keys": {"zephyr_scale": "ZEP-42"},
        "external_links": {"issues": ["ISS-1", "ISS-2"], "pages": ["page-1", "page-2"]},
        "custom_fields": {
            "АС": "АС-1", "Автоматизирован": True,
            "Вид тестирования": ["API", "Regression"], "Команда": 7,
            "Приоритет теста": 1.5, "Статус": "Ready",
            "Ω": "do-not-leak", "Risk": "also-do-not-leak",
        },
    }
    second = copy.deepcopy(case["steps"][0])
    second.update({
        "step_id": "STEP-collect-order", "display_order": 2,
        "action": "Собрать зависимые данные.", "manual_only": True,
        "manual_reason": "Нужен ручной сбор данных.", "operation": None,
        "outputs": [],
        "expectations": [{
            "expectation_id": "EXP-collect-order", "display_order": 1,
            "text": "Данные собраны.", "assertions": [],
        }],
        "inputs": [
            {"input_id": "INPUT-payload", "display_order": 1,
             "target": {"location": "path", "name": "payload", "sensitive": False},
             "source": {"kind": "literal", "value": {"z": 1, "a": "значение"}},
             "semantic_type": {"kind": "json", "type": "object"}},
            {"input_id": "INPUT-prior", "display_order": 2,
             "target": {"location": "query", "name": "prior_status", "sensitive": False},
             "source": {"kind": "step_output", "step_id": "STEP-get-order", "output_id": "status_code"},
             "semantic_type": {"kind": "json", "type": "integer"}},
            {"input_id": "INPUT-fixture", "display_order": 3,
             "target": {"location": "header", "name": "X-Fixture", "sensitive": False},
             "source": {"kind": "fixture", "name": "fixture-name"},
             "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["fixture"]},
            {"input_id": "INPUT-env", "display_order": 4,
             "target": {"location": "body", "pointer": "/token", "sensitive": False},
             "source": {"kind": "environment", "name": "TOKEN"},
             "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["environment"]},
            {"input_id": "INPUT-secret", "display_order": 5,
             "target": {"location": "arg", "name": "secret", "sensitive": True},
             "source": {"kind": "secret_handle", "handle": "actual-secret-handle", "safe_label": "payment-token"},
             "semantic_type": {"kind": "json", "type": "string"}, "type_provenance": ["vault"]},
        ],
    })
    case["steps"].append(second)
    require_valid_canonical_document(document)
    return document


class ZephyrCsvProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = projection_document()

    def render(self, document: dict, profile: str = "zephyr-scale-step-row-24-v1"):
        from tools.test_case_projections import render_zephyr_csv

        return render_zephyr_csv(document, profile)

    def parse(self, payload: bytes) -> list[list[str]]:
        return list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))

    def test_full_http_matches_independently_locked_bom_crlf_golden(self) -> None:
        payload = self.render(self.document).payload
        self.assertEqual(GOLDEN.read_bytes(), payload)
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
        self.assertTrue(payload.endswith(b"\r\n"))
        self.assertEqual(3, payload.count(b"\r\n"))

    def test_24_headers_first_row_mapping_continuation_and_secret_safety(self) -> None:
        projection = self.render(self.document)
        rows = self.parse(projection.payload)
        self.assertEqual(HEADERS, rows[0])
        self.assertEqual(24, len(rows[1]))
        self.assertEqual("ZEP-42", rows[1][0])
        self.assertEqual("High", rows[1][6])
        self.assertEqual("true", rows[1][14])
        self.assertEqual("API, Regression", rows[1][15])
        self.assertEqual("7", rows[1][16])
        self.assertEqual("1.5", rows[1][17])
        self.assertEqual([""] * 19, rows[2][:19])
        self.assertEqual("", rows[1][22])
        self.assertEqual("", rows[1][23])
        self.assertEqual(
            "path:payload = {\"a\":\"значение\",\"z\":1}\n"
            "query:prior_status = status_code, полученный на шаге 1\n"
            "header:X-Fixture = fixture:fixture-name\nbody:/token = env:TOKEN\n"
            "arg:secret = secret:payment-token",
            rows[2][20],
        )
        self.assertNotIn("actual-secret-handle", projection.payload.decode("utf-8-sig"))
        self.assertEqual(
            ('UNMAPPED_ZEPHYR_CUSTOM_FIELD: case_id="TC-http-example", key="Risk"',
             'UNMAPPED_ZEPHYR_CUSTOM_FIELD: case_id="TC-http-example", key="Ω"'),
            projection.warnings,
        )
        self.assertNotIn("do-not-leak", "\n".join(projection.warnings))

    def test_priority_mapping_lists_nulls_formula_defense_and_rfc4180(self) -> None:
        expected = {"CRITICAL": "Highest", "HIGH": "High", "MEDIUM": "Normal", "LOW": "Low"}
        for priority, rendered in expected.items():
            candidate = copy.deepcopy(self.document)
            candidate["test_cases"][0]["priority"] = priority
            self.assertEqual(rendered, self.parse(self.render(candidate).payload)[1][6])
        candidate = copy.deepcopy(self.document)
        management = candidate["test_cases"][0]["management"]
        management.update({"status": " =formula", "folder": None, "components": [], "labels": [], "owner": None, "estimated_time": None})
        management["external_links"] = {"issues": [], "pages": []}
        candidate["test_cases"][0]["title"] = '"quoted", title'
        candidate["test_cases"][0]["steps"][0]["action"] = "\t@formula"
        row = self.parse(self.render(candidate).payload)[1]
        self.assertEqual("' =formula", row[2])
        self.assertEqual([""] * 8, [row[5], row[7], row[8], row[9], row[10], row[11], row[12], row[23]])
        self.assertEqual("'\t@formula", row[19])
        self.assertIn(b'"""quoted"", title"', self.render(candidate).payload)

    def test_formula_whitespace_only_and_safe_values(self) -> None:
        candidate = copy.deepcopy(self.document)
        candidate["test_cases"][0]["management"]["status"] = "\t"
        self.assertEqual("'\t", self.parse(self.render(candidate).payload)[1][2])
        candidate["test_cases"][0]["management"]["status"] = "  safe"
        self.assertEqual("  safe", self.parse(self.render(candidate).payload)[1][2])

    def test_formula_defense_covers_all_prefixes_and_preserves_numeric_custom_values(self) -> None:
        for prefix in "=+-@":
            for leading in ("", " \t\r\n"):
                with self.subTest(prefix=prefix, leading=leading):
                    candidate = copy.deepcopy(self.document)
                    candidate["test_cases"][0]["management"]["status"] = leading + prefix + "formula"
                    self.assertEqual("'" + leading + prefix + "formula", self.parse(self.render(candidate).payload)[1][2])
        for whitespace in ("   ", "\t", "\r", "\n"):
            with self.subTest(whitespace=repr(whitespace)):
                candidate = copy.deepcopy(self.document)
                candidate["test_cases"][0]["management"]["status"] = whitespace
                expected = "'" + whitespace if whitespace != "   " else whitespace
                self.assertEqual(expected, self.parse(self.render(candidate).payload)[1][2])
        candidate = copy.deepcopy(self.document)
        custom = candidate["test_cases"][0]["management"]["custom_fields"]
        custom["Команда"] = -1
        custom["АС"] = "-1"
        row = self.parse(self.render(candidate).payload)[1]
        self.assertEqual("-1", row[16])
        self.assertEqual("'-1", row[13])

    def test_expectations_and_multiple_cases_preserve_rows_without_separators(self) -> None:
        candidate = copy.deepcopy(self.document)
        first_step = candidate["test_cases"][0]["steps"][0]
        first_step["expectations"].append({
            "expectation_id": "EXP-order-extra", "display_order": 2,
            "text": "Второе ожидание.", "assertions": [{
                "assertion_id": "ASSERT-order-extra", "display_order": 1,
                "actual": {"kind": "http_status"}, "operator": "equals",
                "expected": {"kind": "literal", "value": 200},
            }],
        })
        second_case = copy.deepcopy(candidate["test_cases"][0])
        second_case["case_id"] = "TC-http-second"
        second_case["display_order"] = 2
        second_case["title"] = "Второй кейс"
        second_case["steps"][0]["step_id"] = "STEP-get-order-second"
        second_case["steps"][0]["inputs"][0]["input_id"] = "INPUT-order-id-second"
        second_case["steps"][0]["outputs"][0]["output_id"] = "status_second"
        second_case["steps"][0]["expectations"][0]["expectation_id"] = "EXP-order-status-second"
        second_case["steps"][0]["expectations"][0]["assertions"][0]["assertion_id"] = "ASSERT-order-status-second"
        second_case["steps"][0]["expectations"][1]["expectation_id"] = "EXP-order-extra-second"
        second_case["steps"][0]["expectations"][1]["assertions"][0]["assertion_id"] = "ASSERT-order-extra-second"
        second_case["steps"][1]["step_id"] = "STEP-collect-order-second"
        for item in second_case["steps"][1]["inputs"]:
            item["input_id"] += "-second"
        second_case["steps"][1]["inputs"][1]["source"] = {
            "kind": "step_output", "step_id": "STEP-get-order-second", "output_id": "status_second",
        }
        second_case["steps"][1]["expectations"][0]["expectation_id"] = "EXP-collect-order-second"
        candidate["test_cases"].append(second_case)

        rows = self.parse(self.render(candidate).payload)
        self.assertEqual(5, len(rows))
        self.assertEqual("Сервис возвращает HTTP 200.\nВторое ожидание.", rows[1][21])
        self.assertEqual("Второй кейс", rows[3][1])
        self.assertEqual([""] * 19, rows[2][:19])
        self.assertEqual([""] * 19, rows[4][:19])
        self.assertTrue(all(len(row) == 24 for row in rows))

    def test_unknown_profile_precedes_validation_and_invalid_document_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "^unknown Zephyr CSV profile: unknown$"):
            self.render({}, "unknown")
        with self.assertRaises(CanonicalDocumentError):
            self.render({})

    def test_csv_never_mutates_input(self) -> None:
        before = copy.deepcopy(self.document)
        self.render(self.document)
        self.assertEqual(before, self.document)


if __name__ == "__main__":
    unittest.main()
