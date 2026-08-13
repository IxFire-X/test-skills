from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

from tools.canonical_document import CanonicalDocumentError, canonical_bytes  # noqa: E402
from tools.schema_validation import load_json_strict  # noqa: E402
from tools.test_case_projections import render_markdown, render_zephyr_csv  # noqa: E402
import tools.publish_test_case_bundle as publisher  # noqa: E402
from tools.publish_test_case_bundle import (  # noqa: E402
    BundleMismatchError,
    BundlePublicationError,
    build_bundle,
    main,
    publish_bundle,
    verify_bundle,
)


PROFILE = "zephyr-scale-step-row-24-v1"


def document() -> dict:
    return load_json_strict(FIXTURES / "canonical" / "valid" / "full-http.json")


def names(value: dict) -> tuple[str, str, str]:
    prefix = f"{value['document_id']}.r{value['revision']}"
    return prefix + ".json", prefix + ".md", prefix + ".zephyr-scale.csv"


class BundlePublisherTests(unittest.TestCase):
    def test_build_bundle_uses_canonical_and_task_seven_projection_bytes(self) -> None:
        """Break caught: a publisher validates/renders differently from the canonical facades."""
        value = document()
        bundle = build_bundle(value)
        self.assertEqual(canonical_bytes(value), bundle.json_bytes)
        self.assertEqual(render_markdown(value).payload, bundle.markdown_bytes)
        self.assertEqual(render_zephyr_csv(value).payload, bundle.csv_bytes)
        self.assertEqual(
            render_markdown(value).warnings + render_zephyr_csv(value).warnings,
            bundle.warnings,
        )

    def test_unknown_profile_precedes_validation_and_filesystem_actions(self) -> None:
        """Break caught: unknown profiles reach validation or create an output directory."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "not-created"
            with self.assertRaisesRegex(ValueError, "^unknown Zephyr CSV profile: unknown$"):
                publish_bundle({}, output, "unknown")
            self.assertFalse(output.exists())

    def test_unsafe_identity_and_revision_fail_before_output_directory_creation(self) -> None:
        """Break caught: publisher sanitizes invalid identities instead of canonical rejection."""
        invalid_ids = ["TCDOC-UPPER", "TCDOC-a:", "TCDOC-a/", "TCDOC-a\\", "TCDOC-a.", "TCDOC-" + "a" * 200, "TCDOC-a\n"]
        invalid_revisions = [0, 2147483648, 1.0, True]
        for field, values in (("document_id", invalid_ids), ("revision", invalid_revisions)):
            for invalid in values:
                with self.subTest(field=field, invalid=repr(invalid)), tempfile.TemporaryDirectory() as temporary:
                    value = document()
                    value[field] = invalid
                    output = Path(temporary) / "not-created"
                    with self.assertRaises(CanonicalDocumentError):
                        publish_bundle(value, output)
                    self.assertFalse(output.exists())

    def test_publish_uses_exact_direct_child_names_without_case_fold_collision(self) -> None:
        """Break caught: names are normalized, sanitized, or written outside the requested directory."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "bundles"
            first = document()
            receipt = publish_bundle(first, output)
            self.assertEqual(tuple(sorted(names(first))), tuple(sorted(path.name for path in output.iterdir())))
            self.assertEqual(str((output / names(first)[0]).resolve()), receipt.json_path)
            second = document()
            second["document_id"] = "TCDOC-http-example-x"
            publish_bundle(second, output)
            self.assertTrue((output / names(second)[0]).is_file())

    def test_probe_failure_leaves_no_targets_or_current_call_temps(self) -> None:
        """Break caught: an unavailable hard-link primitive falls back to a write or leaves probe debris."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            with mock.patch("tools.publish_test_case_bundle.os.link", side_effect=OSError("unsupported")):
                with self.assertRaises(BundlePublicationError) as raised:
                    publish_bundle(document(), output)
            self.assertEqual("HARDLINK_UNAVAILABLE", raised.exception.code)
            self.assertEqual([], list(output.iterdir()))

    def test_installation_order_closes_all_temps_before_first_link(self) -> None:
        """Break caught: a target is linked before all temporary payload files are complete."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            original_link = os.link
            linked: list[str] = []
            original_temporary = tempfile.NamedTemporaryFile
            tracked: list[object] = []

            class TrackedTemporary:
                def __init__(self, inner: object) -> None:
                    self.inner = inner
                    self.name = inner.name
                    self.closed = False

                def __enter__(self) -> "TrackedTemporary":
                    self.inner.__enter__()
                    return self

                def __exit__(self, *arguments: object) -> object:
                    self.closed = True
                    return self.inner.__exit__(*arguments)

                def write(self, value: bytes) -> int:
                    return self.inner.write(value)

                def flush(self) -> None:
                    self.inner.flush()

            def recording_temporary(*arguments: object, **keywords: object) -> TrackedTemporary:
                temporary_file = TrackedTemporary(original_temporary(*arguments, **keywords))
                tracked.append(temporary_file)
                return temporary_file

            def recorded_link(source: str, target: str, *args: object, **kwargs: object) -> None:
                if Path(target).parent == output and Path(target).name in names(document()):
                    linked.append(Path(target).suffix)
                    self.assertTrue(all(item.closed for item in tracked))
                original_link(source, target, *args, **kwargs)

            with mock.patch("tools.publish_test_case_bundle.tempfile.NamedTemporaryFile", side_effect=recording_temporary), mock.patch("tools.publish_test_case_bundle.os.link", side_effect=recorded_link):
                publish_bundle(document(), output)
            self.assertEqual([".json", ".md", ".csv"], linked)

    def test_identical_rerun_returns_same_receipt_without_rewriting(self) -> None:
        """Break caught: a rerun overwrites immutable target bytes or changes its receipt."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            first = publish_bundle(document(), output)
            before = {path.name: path.stat().st_ino for path in output.iterdir()}
            second = publish_bundle(document(), output)
            self.assertEqual(first, second)
            self.assertEqual(before, {path.name: path.stat().st_ino for path in output.iterdir()})

    def test_preexisting_different_artifact_stops_before_any_change(self) -> None:
        """Break caught: collision preflight fills a missing artifact or overwrites an existing one."""
        for index, label in enumerate(("json", "markdown", "csv")):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                value = document()
                target = output / names(value)[index]
                target.write_bytes(b"different")
                with self.assertRaises(BundleMismatchError) as raised:
                    publish_bundle(value, output)
                self.assertEqual((label,), raised.exception.mismatches)
                self.assertEqual(b"different", target.read_bytes())
                self.assertEqual(1, len(list(output.iterdir())))

    def test_partial_prefix_recovers_only_missing_suffix_and_verify_reports_all_mismatches(self) -> None:
        """Break caught: recovery rewrites an identical prefix or verify mutates/short-circuits mismatches."""
        value = document()
        bundle = build_bundle(value)
        for count in (0, 1, 2, 3):
            with self.subTest(existing=count), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary)
                for filename, payload in zip(names(value)[:count], (bundle.json_bytes, bundle.markdown_bytes, bundle.csv_bytes)):
                    (output / filename).write_bytes(payload)
                receipt = publish_bundle(value, output)
                self.assertEqual(3, len(list(output.iterdir())))
                self.assertEqual(receipt, verify_bundle(value, output))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / names(value)[1]).write_bytes(b"wrong")
            with self.assertRaises(BundleMismatchError) as raised:
                verify_bundle(value, output)
            self.assertEqual(("json", "markdown", "csv"), raised.exception.mismatches)
            self.assertEqual([names(value)[1]], [path.name for path in output.iterdir()])

    def test_final_readback_uses_read_bytes_and_detects_tamper(self) -> None:
        """Break caught: a receipt hashes expected bytes without confirming installed targets."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            receipt = publish_bundle(document(), output)
            self.assertEqual("sha256:" + hashlib.sha256(Path(receipt.json_path).read_bytes()).hexdigest(), receipt.document_sha256)
            Path(receipt.markdown_path).write_bytes(b"tampered")
            with self.assertRaises(BundleMismatchError) as raised:
                verify_bundle(document(), output)
            self.assertEqual(("markdown",), raised.exception.mismatches)

    def test_publish_final_readback_tamper_prevents_receipt(self) -> None:
        """Break caught: publication returns an expected-byte receipt after a target is changed."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            original_link = os.link

            def tampering_link(source: str, target: str, *args: object, **kwargs: object) -> None:
                original_link(source, target, *args, **kwargs)
                if Path(target).name.endswith(".zephyr-scale.csv"):
                    Path(target).write_bytes(b"tampered")

            with mock.patch("tools.publish_test_case_bundle.os.link", side_effect=tampering_link):
                with self.assertRaises(BundleMismatchError) as raised:
                    publish_bundle(document(), output)
            self.assertEqual(("csv",), raised.exception.mismatches)
            self.assertEqual([], [path for path in output.iterdir() if path.name.startswith("tmp")])

    def test_current_call_temp_cleanup_on_write_failure_and_mid_link_collision(self) -> None:
        """Break caught: failed staging/linking leaves temporary payload files or installs a suffix."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            original_temporary = tempfile.NamedTemporaryFile
            calls = 0

            def failing_second_payload(*arguments: object, **keywords: object):
                nonlocal calls
                calls += 1
                if calls == 3:  # probe, JSON temp, then Markdown temp
                    raise OSError("synthetic write failure")
                return original_temporary(*arguments, **keywords)

            with mock.patch("tools.publish_test_case_bundle.tempfile.NamedTemporaryFile", side_effect=failing_second_payload):
                with self.assertRaises(BundlePublicationError) as raised:
                    publish_bundle(document(), output)
            self.assertEqual("TEMPORARY_WRITE_FAILED", raised.exception.code)
            self.assertEqual([], list(output.iterdir()))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            original_link = os.link

            def colliding_json(source: str, target: str, *args: object, **kwargs: object) -> None:
                if Path(target).name.endswith(".json"):
                    Path(target).write_bytes(b"winner")
                original_link(source, target, *args, **kwargs)

            with mock.patch("tools.publish_test_case_bundle.os.link", side_effect=colliding_json):
                with self.assertRaises(BundleMismatchError) as raised:
                    publish_bundle(document(), output)
            self.assertEqual(("json",), raised.exception.mismatches)
            self.assertEqual([names(document())[0]], [path.name for path in output.iterdir()])

    def test_cli_publish_verify_and_exactly_three_files(self) -> None:
        """Break caught: CLI accepts envelopes, emits extra output, or does not publish all exact projections."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            input_path = directory / "input.json"
            output = directory / "output"
            input_path.write_bytes(canonical_bytes(document()))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(0, main(["--input", str(input_path), "--output-dir", str(output)]))
            published = json.loads(stdout.getvalue())
            self.assertEqual(sorted(("csv_path", "csv_profile", "csv_sha256", "document_id", "document_sha256", "json_path", "markdown_path", "markdown_sha256", "revision")), sorted(published))
            self.assertEqual(3, len(list(output.iterdir())))
            self.assertEqual(canonical_bytes(document()), (output / names(document())[0]).read_bytes())
            self.assertEqual(render_markdown(document()).payload, (output / names(document())[1]).read_bytes())
            self.assertEqual(render_zephyr_csv(document()).payload, (output / names(document())[2]).read_bytes())
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(0, main(["--input", str(input_path), "--output-dir", str(output), "--verify-only"]))
            self.assertEqual(published, json.loads(stdout.getvalue()))

    def test_cli_strict_input_v21_and_secret_safe_errors(self) -> None:
        """Break caught: CLI leaks input details or collapses strict/V2.1 failures into success."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            output = directory / "output"
            for raw in (b'\xef\xbb\xbf{}', b'{"x":1,"x":2}', b'{"secret":"HANDLE-MUST-NOT-LEAK",'):
                input_path = directory / "bad.json"
                input_path.write_bytes(raw)
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(2, main(["--input", str(input_path), "--output-dir", str(output)]))
                self.assertNotIn("HANDLE-MUST-NOT-LEAK", stdout.getvalue())
                self.assertEqual({"status": "error", "errors": [{"path": "", "code": "INPUT_JSON_UNREADABLE", "message": "Input must be strict UTF-8 canonical JSON."}]}, json.loads(stdout.getvalue()))
            legacy = FIXTURES / "canonical" / "invalid" / "legacy-v2.1.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(1, main(["--input", str(legacy), "--output-dir", str(output)]))
            self.assertEqual({"status": "invalid", "errors": [{"path": "/schema_version", "code": "V2_1_BREAKING_CHANGE", "message": "schema version 2.1.0 is incompatible with canonical model 3.0.0"}]}, json.loads(stdout.getvalue()))

    def test_cli_unknown_profile_precedes_input_loading_with_safe_exit_two(self) -> None:
        """Break caught: an unknown profile reads untrusted input before deterministic CLI rejection."""
        stdout = io.StringIO()
        with mock.patch("tools.publish_test_case_bundle.load_json_strict") as loader, redirect_stdout(stdout):
            self.assertEqual(2, main(["--input", "unreadable.json", "--output-dir", "output", "--csv-profile", "unknown"]))
        loader.assert_not_called()
        self.assertEqual(
            {"status": "error", "errors": [{"path": "", "code": "TARGET_INSTALL_FAILED", "message": "Bundle target could not be installed."}]},
            json.loads(stdout.getvalue()),
        )

    def test_public_exception_data_is_read_only_and_remains_intact(self) -> None:
        """Break caught: callers can mutate deterministic public exception data after construction."""
        mismatch = BundleMismatchError(("json", "csv"))
        publication = BundlePublicationError("TARGET_READ_FAILED")
        for instance, attribute, replacement, expected in (
            (mismatch, "mismatches", ("markdown",), ("json", "csv")),
            (publication, "code", "TARGET_INSTALL_FAILED", "TARGET_READ_FAILED"),
            (publication, "safe_message", "changed", "Bundle target could not be read."),
        ):
            with self.subTest(attribute=attribute):
                with self.assertRaises(AttributeError):
                    setattr(instance, attribute, replacement)
                self.assertEqual(expected, getattr(instance, attribute))

    def test_output_directory_errors_and_verify_only_have_no_writes(self) -> None:
        """Break caught: verify creates output state or unavailable output paths leak primitive errors."""
        with tempfile.TemporaryDirectory() as temporary:
            output_file = Path(temporary) / "not-a-directory"
            output_file.write_bytes(b"unrelated")
            for function in (publish_bundle, verify_bundle):
                with self.subTest(function=function.__name__), self.assertRaises(BundlePublicationError) as raised:
                    function(document(), output_file)
                self.assertEqual("OUTPUT_DIRECTORY_ERROR", raised.exception.code)
            missing = Path(temporary) / "missing"
            with self.assertRaises(BundleMismatchError) as raised:
                verify_bundle(document(), missing)
            self.assertEqual(("json", "markdown", "csv"), raised.exception.mismatches)
            self.assertFalse(missing.exists())

    def test_concurrent_identical_and_conflicting_publishers(self) -> None:
        """Break caught: concurrent publishers produce a mixed successful receipt or overwrite a winner."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            barrier = threading.Barrier(2)
            original_preflight = publisher._preflight
            outcomes: list[object] = []

            def after_empty_preflight(*arguments: object, **keywords: object):
                result = original_preflight(*arguments, **keywords)
                barrier.wait(timeout=5)
                return result

            def run(value: dict) -> None:
                try:
                    outcomes.append(publish_bundle(value, output))
                except Exception as error:  # Exact type asserted below.
                    outcomes.append(error)

            with mock.patch("tools.publish_test_case_bundle._preflight", side_effect=after_empty_preflight):
                threads = [threading.Thread(target=run, args=(document(),)) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
            self.assertTrue(all(not isinstance(item, Exception) for item in outcomes))
            self.assertEqual(outcomes[0], outcomes[1])
            self.assertEqual(3, len(list(output.iterdir())))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            winner, loser = document(), document()
            loser["metadata"]["author"] = "OTHER"
            barrier = threading.Barrier(2)
            outcomes = []
            with mock.patch("tools.publish_test_case_bundle._preflight", side_effect=after_empty_preflight):
                threads = [threading.Thread(target=run, args=(value,)) for value in (winner, loser)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
            self.assertEqual(1, sum(not isinstance(item, Exception) for item in outcomes))
            self.assertEqual(1, sum(isinstance(item, BundleMismatchError) for item in outcomes))

    def test_version_skew_projection_collision_does_not_return_mixed_bundle(self) -> None:
        """Break caught: same JSON with different projection bytes produces two successful mixed receipts."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            barrier = threading.Barrier(2)
            original_preflight = publisher._preflight
            original_build = publisher.build_bundle
            outcomes: list[object] = []
            build_count = 0

            def skewed_build(value: dict, profile: str = PROFILE):
                nonlocal build_count
                bundle = original_build(value, profile)
                build_count += 1
                if build_count == 2:
                    return type(bundle)(bundle.json_bytes, bundle.markdown_bytes, bundle.csv_bytes + b"skew", bundle.warnings)
                return bundle

            def after_empty_preflight(*arguments: object, **keywords: object):
                result = original_preflight(*arguments, **keywords)
                barrier.wait(timeout=5)
                return result

            def run() -> None:
                try:
                    outcomes.append(publish_bundle(document(), output))
                except Exception as error:
                    outcomes.append(error)

            with mock.patch("tools.publish_test_case_bundle.build_bundle", side_effect=skewed_build), mock.patch("tools.publish_test_case_bundle._preflight", side_effect=after_empty_preflight):
                threads = [threading.Thread(target=run) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
                    self.assertFalse(thread.is_alive())
            self.assertEqual(1, sum(not isinstance(item, Exception) for item in outcomes))
            self.assertEqual(1, sum(isinstance(item, BundleMismatchError) for item in outcomes))
            self.assertEqual(canonical_bytes(document()), (output / names(document())[0]).read_bytes())
            self.assertEqual(render_markdown(document()).payload, (output / names(document())[1]).read_bytes())

    def test_unrelated_files_remain_and_no_manifest_is_created_or_forbidden_code_remains(self) -> None:
        """Break caught: publishing alters unrelated content or retains a fourth-artifact/overwrite implementation."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            unrelated = output / "keep.txt"
            unrelated.write_bytes(b"keep")
            publish_bundle(document(), output)
            self.assertEqual(b"keep", unrelated.read_bytes())
            self.assertEqual(4, len(list(output.iterdir())))
            self.assertFalse(any("manifest" in path.name for path in output.iterdir()))
        result = subprocess.run(
            ["rg", "-n", "os\\.replace|FIELDNAMES|_reconstruct", "skills/tc-generator/scripts/export_test_cases_csv.py", "tools/publish_test_case_bundle.py"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
