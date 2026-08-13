from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "canonical"
GOLDEN = FIXTURES / "golden"
sys.path.insert(0, str(ROOT))

from tools.canonical_document import (  # noqa: E402
    canonical_bytes,
    document_sha256,
    load_canonical_document,
)
from tools.schema_validation import load_json_strict  # noqa: E402


class CanonicalBytesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = load_json_strict(FIXTURES / "valid" / "full-http.json")

    def test_matches_exact_golden_bytes_and_locked_digest(self) -> None:
        """A serializer mutation must change either bytes or the independently locked digest."""
        payload = canonical_bytes(self.document)
        golden = (GOLDEN / "full-http.canonical-json.bin").read_bytes()
        self.assertEqual(golden, payload)
        self.assertEqual(
            "sha256:a3155ce1a219e851beadedac0f4e54802336e5a128b5e87b147fbffb7a98b5a3",
            document_sha256(self.document),
        )
        self.assertEqual(
            "sha256:a3155ce1a219e851beadedac0f4e54802336e5a128b5e87b147fbffb7a98b5a3",
            "sha256:" + hashlib.sha256(golden).hexdigest(),
        )

    def test_object_key_order_does_not_change_bytes(self) -> None:
        """Removing object-key sorting would make this semantic document unstable."""
        reversed_keys = dict(reversed(list(self.document.items())))
        self.assertEqual(canonical_bytes(self.document), canonical_bytes(reversed_keys))

    def test_array_order_changes_bytes(self) -> None:
        """Sorting arrays would lose the contract's order-significant input order."""
        changed = copy.deepcopy(self.document)
        changed["test_cases"][0]["categories"] = list(reversed(changed["test_cases"][0]["categories"]))
        self.assertNotEqual(canonical_bytes(self.document), canonical_bytes(changed))

    def test_unicode_is_not_normalized(self) -> None:
        """Normalizing Unicode would collapse two distinct JSON strings."""
        composed = {"text": "é"}
        decomposed = {"text": "e\u0301"}
        self.assertNotEqual(canonical_bytes(composed), canonical_bytes(decomposed))

    def test_no_bom_or_terminal_lf(self) -> None:
        """Transport text markers are not part of canonical document bytes."""
        payload = canonical_bytes(self.document)
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(payload.endswith(b"\n"))

    def test_non_finite_number_is_rejected(self) -> None:
        """Allowing a non-finite number would create non-standard JSON bytes."""
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                canonical_bytes({"value": value})

    def test_envelope_digest_is_not_document_digest(self) -> None:
        """Hashing a stage envelope instead of the bare document changes revision identity."""
        envelope = {"schema_version": "3.0.0", "artifacts": {"canonical_document": self.document}}
        envelope_digest = "sha256:" + hashlib.sha256(canonical_bytes(envelope)).hexdigest()
        self.assertNotEqual(document_sha256(self.document), envelope_digest)

    def test_load_canonical_document_validates_structure_and_physical_order(self) -> None:
        """Skipping Task 1 diagnostics would accept malformed or physically reordered input."""
        self.assertEqual(
            self.document,
            load_canonical_document(FIXTURES / "valid" / "full-http.json"),
        )
        mutations = [
            ("missing required structure", lambda document: document.pop("metadata")),
            (
                "noncanonical physical order",
                lambda document: document["test_cases"][0].__setitem__("display_order", 2),
            ),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                candidate = copy.deepcopy(self.document)
                mutate(candidate)
                path = Path(temporary) / "candidate.json"
                path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_canonical_document(path)


if __name__ == "__main__":
    unittest.main()
