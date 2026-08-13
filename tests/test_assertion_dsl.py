"""Portable assertion DSL contract tests; expected values are hand-derived."""

from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
from unittest.mock import patch

from tools import assertion_dsl
from tools.assertion_dsl import (
    AssertionResult,
    EvaluationContext,
    ValueState,
    evaluate_assertion,
    portable_fullmatch,
)


FIXTURE = Path(__file__).parent / "fixtures" / "portable-regex-v1" / "cases.json"


def corpus():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def value_resolver(source):
    return ValueState.value(source["value"])


def actual(value):
    return {"kind": "test_actual", "value": value}


def assertion(operator, actual_value, expected=None):
    row = {"assertion_id": "ASSERT-test", "actual": actual(actual_value), "operator": operator}
    if expected is not None:
        row["expected"] = expected
    return row


class PortableRegexV1Tests(unittest.TestCase):
    def test_shared_regex_corpus(self) -> None:
        """A broken grammar production or Unicode scalar matching changes corpus results."""
        for row in corpus()["portable_regex_v1"]["cases"]:
            with self.subTest(row=row["name"]):
                if row["valid"]:
                    self.assertEqual(row["matches"], portable_fullmatch(row["pattern"], row["value"]))
                else:
                    with self.assertRaises(ValueError):
                        portable_fullmatch(row["pattern"], row["value"])

    def test_ambiguous_repeats_use_bounded_nfa_transitions(self) -> None:
        """Suffix-position expansion regresses to quadratic/exponential work on a*a*."""
        matched, metrics = assertion_dsl._portable_fullmatch_metrics("a*a*a*", "a" * 4000)
        self.assertTrue(matched)
        self.assertLessEqual(metrics["transition_steps"], 4000 * metrics["state_count"])
        self.assertLessEqual(metrics["max_active_states"], metrics["state_count"])

    def test_unpaired_surrogates_are_rejected(self) -> None:
        """Treating a surrogate code unit as a scalar violates the V1 alphabet."""
        with self.assertRaises(ValueError):
            portable_fullmatch("\ud800", "x")
        with self.assertRaises(ValueError):
            portable_fullmatch("x", "\ud800")


class ValueStateTests(unittest.TestCase):
    def test_missing_is_distinct_from_json_null_and_not_json_serializable(self) -> None:
        """Serializing MISSING or conflating it with null would create false assertions."""
        missing = ValueState.missing()
        null = ValueState.value(None)
        self.assertNotEqual(missing, null)
        self.assertTrue(missing.is_missing)
        self.assertFalse(null.is_missing)
        with self.assertRaises(TypeError):
            json.dumps(missing.data)
        self.assertEqual("null", json.dumps(null.data))

    def test_direct_construction_enforces_tag_data_invariant(self) -> None:
        """A serializable payload on a missing tag could leak or counterfeit MISSING."""
        with self.assertRaises(ValueError):
            ValueState(True, None)
        with self.assertRaises(ValueError):
            ValueState(True, "serializable")
        with self.assertRaises(ValueError):
            ValueState(False, missing_data())

    def test_public_result_and_context_are_immutable(self) -> None:
        """Mutable evaluation results or schema context could change a completed verdict."""
        context = EvaluationContext(lambda uri: b"{}")
        result = AssertionResult("PASSED", None, None)
        with self.assertRaises(FrozenInstanceError):
            context.resolve_schema = lambda uri: b"{}"
        with self.assertRaises(FrozenInstanceError):
            result.status = "FAILED"


class AssertionOperatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = EvaluationContext(lambda uri: b"{}")

    def evaluate(self, row, actual_state=None, expected_state=None):
        def resolve_actual(source):
            return actual_state if actual_state is not None else value_resolver(source)

        def resolve_expected(source):
            return expected_state if expected_state is not None else value_resolver(source)

        return evaluate_assertion(row, resolve_actual, resolve_expected, self.context)

    def test_shared_assertion_corpus(self) -> None:
        """Changing an operator branch alters the language-neutral assertion verdicts."""
        for row in corpus()["assertion_v1"]["cases"]:
            actual_state = ValueState.missing() if row["actual"]["state"] == "missing" else ValueState.value(row["actual"]["value"])
            result = self.evaluate(assertion(row["operator"], row["actual"].get("value"), row.get("expected")), actual_state=actual_state)
            with self.subTest(row=row["name"]):
                self.assertEqual(row["result"]["status"], result.status)
                self.assertEqual(row["result"]["code"], result.code)
                self.assertEqual(row["result"]["message"], result.message)

    def test_operand_and_resolver_errors_are_deterministic(self) -> None:
        """Invalid operator shapes and resolver crashes must not become false passes."""
        bad_operator = self.evaluate(assertion("unknown", "x", {"kind": "literal", "value": "x"}))
        self.assertEqual(AssertionResult("ERROR", "INVALID_ASSERTION", "Unsupported assertion operator."), bad_operator)

        def raising_actual(source):
            raise RuntimeError("provider failure")

        result = evaluate_assertion(assertion("equals", "x", {"kind": "literal", "value": "x"}), raising_actual, value_resolver, self.context)
        self.assertEqual(AssertionResult("ERROR", "ACTUAL_RESOLUTION_ERROR", "Could not resolve actual value."), result)


class SchemaMatchesTests(unittest.TestCase):
    def _evaluate(self, expected, actual_value, resolver):
        return evaluate_assertion(
            assertion("schema_matches", actual_value, expected),
            value_resolver,
            value_resolver,
            EvaluationContext(resolver),
        )

    def test_schema_matches_uses_only_exact_authorized_uri(self) -> None:
        """Changing the URI passed to the local resolver could load an unintended schema."""
        schema = b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","required":["id"]}'
        digest = "sha256:" + hashlib.sha256(schema).hexdigest()
        calls = []

        def resolver(uri):
            calls.append(uri)
            if uri == "urn:approved:schema":
                return schema
            raise AssertionError("unexpected URI")

        expected = {"kind": "schema_ref", "uri": "urn:approved:schema", "sha256": digest, "draft": "2020-12"}
        self.assertEqual(AssertionResult("PASSED", None, None), self._evaluate(expected, {"id": 1}, resolver))
        self.assertEqual(["urn:approved:schema"], calls)

    def test_schema_mismatch_is_failed_but_schema_setup_errors_are_errors(self) -> None:
        """A nonmatching instance differs from an unauthorized or invalid schema."""
        schema = b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"array"}'
        digest = "sha256:" + hashlib.sha256(schema).hexdigest()
        expected = {"kind": "schema_ref", "uri": "urn:schema", "sha256": digest, "draft": "2020-12"}
        self.assertEqual(AssertionResult("FAILED", "ASSERTION_FAILED", "Assertion evaluated to false."), self._evaluate(expected, {}, lambda uri: schema))

        bad_digest = dict(expected, sha256="sha256:" + "A" * 64)
        self.assertEqual(AssertionResult("ERROR", "INVALID_SCHEMA_PIN", "Schema SHA-256 pin must be lowercase."), self._evaluate(bad_digest, {}, lambda uri: schema))

        invalid_json = b'{"$schema":'
        invalid_expected = dict(expected, sha256="sha256:" + hashlib.sha256(invalid_json).hexdigest())
        self.assertEqual(AssertionResult("ERROR", "INVALID_SCHEMA", "Schema bytes are not strict JSON."), self._evaluate(invalid_expected, {}, lambda uri: invalid_json))

        duplicate_key = b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object","type":"array"}'
        duplicate_expected = dict(expected, sha256="sha256:" + hashlib.sha256(duplicate_key).hexdigest())
        self.assertEqual(AssertionResult("ERROR", "INVALID_SCHEMA", "Schema bytes are not strict JSON."), self._evaluate(duplicate_expected, {}, lambda uri: duplicate_key))

    def test_schema_digest_mismatch_missing_bytes_and_wrong_draft_are_errors(self) -> None:
        """Parsing before pinning or accepting a different draft would violate schema pinning."""
        schema = b'{"$schema":"https://json-schema.org/draft/2020-12/schema","type":"object"}'
        expected = {"kind": "schema_ref", "uri": "urn:schema", "sha256": "sha256:" + "0" * 64, "draft": "2020-12"}
        self.assertEqual(AssertionResult("ERROR", "SCHEMA_DIGEST_MISMATCH", "Schema bytes do not match the declared SHA-256 pin."), self._evaluate(expected, {}, lambda uri: schema))
        self.assertEqual(AssertionResult("ERROR", "SCHEMA_RESOLUTION_ERROR", "Could not resolve authorized schema bytes."), self._evaluate(expected, {}, lambda uri: (_ for _ in ()).throw(KeyError(uri))))
        wrong_draft = dict(expected, draft="2019-09")
        self.assertEqual(AssertionResult("ERROR", "INVALID_SCHEMA_DRAFT", "schema_matches requires Draft 2020-12."), self._evaluate(wrong_draft, {}, lambda uri: schema))

    def test_nonlocal_dynamic_ref_is_rejected_before_any_retrieval(self) -> None:
        """Allowing a remote $dynamicRef could make validation retrieve attacker-controlled bytes."""
        schema = b'{"$schema":"https://json-schema.org/draft/2020-12/schema","$dynamicRef":"https://example.invalid/schema"}'
        expected = {"kind": "schema_ref", "uri": "urn:approved", "sha256": "sha256:" + hashlib.sha256(schema).hexdigest(), "draft": "2020-12"}
        attempts = []

        def attempted(name):
            def fail(*args, **kwargs):
                attempts.append(name)
                raise AssertionError("network retrieval attempted")
            return fail

        with patch("urllib.request.urlopen", attempted("urlopen")), patch.object(socket, "create_connection", attempted("socket")):
            result = self._evaluate(expected, {}, lambda uri: schema)
        self.assertEqual(AssertionResult("ERROR", "INVALID_SCHEMA", "Schema contains a non-local reference."), result)
        self.assertEqual([], attempts)


def missing_data():
    return ValueState.missing().data


if __name__ == "__main__":
    unittest.main()
