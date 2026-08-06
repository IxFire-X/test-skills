import json
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
from jsonschema import Draft202012Validator


def _scan(root, project, target, output=None):
    command = [
        sys.executable,
        str(root / "tools" / "scan_project.py"),
        "--project", str(project),
        "--target", target,
    ]
    if output is not None:
        command.extend(["--output", str(output)])
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _validate_scan_output(root, completed):
    document = json.loads(completed.stdout)
    schema = json.loads((root / "schemas" / "scan-project-output.schema.json").read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(document)) == []
    return document


def _python_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'demo'\ndependencies = ['fastapi']\n",
        encoding="utf-8",
    )
    target = tmp_path / "src" / "api.py"
    target.parent.mkdir()
    target.write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    return target


def test_detects_python_from_pyproject_manifest(scanner, tmp_path):
    """Catches missing Python stack detection when only pyproject.toml declares it."""
    _python_project(tmp_path)

    detected = scanner.detect_stack(str(tmp_path), "src/api.py")

    assert detected["stack"]["language"] == "python"
    assert detected["stack"]["framework"] == "fastapi"
    assert detected["stack"]["detection"]["manifest"] == "pyproject.toml"


def test_scanner_rejects_output_traversal_before_writing(root, tmp_path):
    """Catches writing a persistent scanner artifact outside exact docs/to_do."""
    _python_project(tmp_path)
    escaped = tmp_path / "docs" / "to_do" / ".." / "escaped.md"

    completed = _scan(root, tmp_path, "src/api.py", escaped)

    report = json.loads(completed.stdout)
    assert completed.returncode != 0
    assert report["status"] == "error"
    assert not (tmp_path / "docs" / "escaped.md").exists()


def test_scanner_ignores_java_comments_and_text_blocks_but_preserves_source(scanner, tmp_path):
    """Catches annotation-like Java comments/text blocks being emitted as endpoints or altered source."""
    (tmp_path / "pom.xml").write_text("<project><artifactId>demo</artifactId></project>", encoding="utf-8")
    source = '''package demo;
// @GetMapping("/comment")
/* @PostMapping("/block-comment") */
class Demo {
  String sample = """
    @GetMapping("/text-block")
    <![CDATA[leave me unchanged]]>
    """;
  @GetMapping("/live")
  String live() { return "ok"; }
}
'''
    target = tmp_path / "Demo.java"
    target.write_text(source, encoding="utf-8")

    assert scanner._extract_endpoints(str(tmp_path), ["Demo.java"], "spring-boot") == ["/live"]
    rendered = scanner.render_source_block(str(tmp_path), ["Demo.java"])
    assert ET.fromstring(rendered).find("file").text == source


def test_java_endpoint_lexer_ignores_comments_text_strings_and_chars(scanner, tmp_path):
    """Catches mapping-shaped literals suppressing live code or becoming false endpoints."""
    source = '''class Demo {
  String slash = "// @GetMapping(\\"/inside-string\\")";
  String annotation = "@PostMapping(\\"/quoted\\")";
  char quote = '\\'';
  // @GetMapping("/comment")
  /* @GetMapping("/block") */
  String text = """ @GetMapping("/text-block") """;
  @GetMapping("/live") String live() { return "// still code after string"; }
}
'''
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    assert scanner._extract_endpoints(str(tmp_path), ["Demo.java"], "spring-boot") == ["/live"]


def test_java_endpoint_lexer_keeps_escaped_text_block_delimiter_masked(scanner, tmp_path):
    """Catches an escaped text-block delimiter exposing a fake mapping as code."""
    source = '''class Demo {
  String text = """
    \\""" @GetMapping("/fake")
    still text
    """;
  @GetMapping("/live") String live() { return "ok"; }
}
'''
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    assert scanner._extract_endpoints(str(tmp_path), ["Demo.java"], "spring-boot") == ["/live"]


def test_java_mapping_arguments_accept_only_paths_values_and_positionals(scanner, tmp_path):
    """Catches metadata strings such as produces or headers being treated as endpoints."""
    source = '''class Demo {
  @RequestMapping(produces = "application/json", headers = "X-Trace") void metadataOnly() {}
  @GetMapping(path = {"/path-a", "/path-b"}, produces = "application/json") void paths() {}
  @PostMapping(value = "/value", consumes = "application/json") void value() {}
  @DeleteMapping("/direct") void direct() {}
  @Path("/jax") void jax() {}
}
'''
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    assert scanner._extract_endpoints(str(tmp_path), ["Demo.java"], "spring-boot") == [
        "/path-a", "/path-b", "/value", "/direct", "/jax",
    ]


def test_java_mapping_named_keys_must_be_lexical_code(scanner, tmp_path):
    """Catches path/value-shaped metadata, comments, and text blocks becoming endpoints."""
    source = '''class Demo {
  @RequestMapping(headers = "x, path=\\"/fake-header\\"") void header() {}
  @RequestMapping(/* , value="/fake-comment" */ produces = "application/json") void comment() {}
  @RequestMapping(produces = """
    metadata, path="/fake-text"
    """) void text() {}
  @GetMapping(headers = "X-Trace", path = "/real") void real() {}
  @DeleteMapping("/direct") void direct() {}
  @Path("/jax") void jax() {}
}
'''
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    assert scanner._extract_endpoints(str(tmp_path), ["Demo.java"], "spring-boot") == [
        "/real", "/direct", "/jax",
    ]


def test_source_envelope_round_trips_delimiter_shaped_java_source(scanner, tmp_path):
    """Catches source evidence corrupting XML-like file/source framing delimiters."""
    source = '''class Demo {
  String closeFile = "</file>";
  String closeRoot = "</source_code_and_diff>";
  String cdata = "]]>"];
  String escaped = "\\\" // @GetMapping(\\\"/not-an-endpoint\\\")";
  char quote = '\\'';
}
'''
    (tmp_path / "Demo.java").write_text(source, encoding="utf-8")

    envelope = scanner.render_source_block(str(tmp_path), ["Demo.java"])

    parsed = ET.fromstring(envelope)
    assert parsed.find("file").text == source


def test_scanner_includes_direct_project_local_java_import(root, tmp_path):
    """Catches omitting a controller's directly imported project model from source evidence."""
    (tmp_path / "pom.xml").write_text("<project><artifactId>demo</artifactId></project>", encoding="utf-8")
    controller = tmp_path / "src" / "main" / "java" / "example" / "web" / "StudentController.java"
    model = tmp_path / "src" / "main" / "java" / "example" / "model" / "Student.java"
    controller.parent.mkdir(parents=True)
    model.parent.mkdir(parents=True)
    controller.write_text("package example.web;\nimport example.model.Student;\nclass StudentController { Student value; }\n", encoding="utf-8")
    model.write_text("package example.model;\nclass Student {}\n", encoding="utf-8")

    completed = _scan(root, tmp_path, "src/main/java/example/web/StudentController.java")

    report = _validate_scan_output(root, completed)
    assert completed.returncode == 0
    assert report["files_extracted"] == [
        "src/main/java/example/web/StudentController.java",
        "src/main/java/example/model/Student.java",
    ]


def test_missing_target_is_error_and_does_not_create_skillsrc(root, tmp_path):
    """Catches a missing target causing scanner-side project mutations."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

    completed = _scan(root, tmp_path, "missing.py")

    report = _validate_scan_output(root, completed)
    assert completed.returncode != 0
    assert report["status"] == "error"
    assert not (tmp_path / ".skillsrc").exists()


def test_scan_is_read_only_and_output_requires_exact_docs_to_do(root, tmp_path):
    """Catches default .skillsrc writes and accepting a near-match persistent directory."""
    _python_project(tmp_path)
    skillsrc = tmp_path / ".skillsrc"
    original = "project:\n  language: python\n"
    skillsrc.write_text(original, encoding="utf-8")
    near_match = tmp_path / "docs" / "to_do_backup" / "result.md"

    completed = _scan(root, tmp_path, "src/api.py", near_match)

    report = _validate_scan_output(root, completed)
    assert completed.returncode != 0
    assert report["skillsrc_updated"] is False
    assert skillsrc.read_text(encoding="utf-8") == original
    assert not near_match.exists()


def test_scanner_writes_only_to_exact_docs_to_do(root, tmp_path):
    """Catches rejecting the canonical confined location needed for scanner evidence."""
    _python_project(tmp_path)
    output = tmp_path / "docs" / "to_do" / "context.md"

    completed = _scan(root, tmp_path, "src/api.py", output)

    report = _validate_scan_output(root, completed)
    assert completed.returncode == 0
    assert report["output_file"] == str(output).replace("\\", "/")
    assert output.exists()


def test_scanner_rejects_escape_like_targets_before_reading(root, tmp_path):
    """Catches absolute, Windows drive, UNC, and traversal target escape attempts."""
    _python_project(tmp_path)
    outside = tmp_path.parent / "README.md"
    outside.write_text("outside secret", encoding="utf-8")

    for target in ("../README.md", "schemas/../../README.md", str(outside), "C:README.md", r"\\server\share\README.md"):
        completed = _scan(root, tmp_path, target)
        report = _validate_scan_output(root, completed)
        assert completed.returncode == 1
        assert report["status"] == "error"
        assert report["files_extracted"] == []
        assert any("outside project" in message.lower() or "invalid target" in message.lower() for message in report["errors"])


def test_scanner_rejects_symlink_target_that_resolves_outside_project(root, tmp_path):
    """Catches direct candidates that look local but resolve through a symlink outside root."""
    _python_project(tmp_path)
    outside = tmp_path.parent / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    link = tmp_path / "src" / "linked.py"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable on this platform")

    completed = _scan(root, tmp_path, "src/linked.py")
    report = _validate_scan_output(root, completed)
    assert completed.returncode == 1
    assert report["status"] == "error"
    assert report["files_extracted"] == []


def test_scanner_keeps_generated_artifact_in_its_json_stdout(root, tmp_path):
    """Catches source/analytics content being available only by parsing operational stderr."""
    _python_project(tmp_path)

    completed = _scan(root, tmp_path, "src/api.py")
    report = _validate_scan_output(root, completed)

    assert completed.returncode == 0
    assert "<source_code_and_diff>" in report["artifact"]
    assert "<analytics_documentation>" in report["artifact"]
    assert completed.stderr == ""


def test_confined_path_rejects_outside_and_companion_append_is_guarded(scanner, tmp_path):
    """Catches companion candidates escaping root even when discovered separately from target."""
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "models.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    target = project / "api.py"
    target.write_text("x = 1\n", encoding="utf-8")

    assert scanner._confined_path(str(project), str(outside)) is None
    assert scanner.extract_companions(str(project), str(target), "python") == []


def test_render_source_block_does_not_read_unconfined_paths(scanner, tmp_path, monkeypatch):
    """Catches rendering an attacker-supplied relative path after discovery guards."""
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    reads = []
    original = scanner._read_text
    monkeypatch.setattr(scanner, "_read_text", lambda path: reads.append(path) or original(path))

    rendered = scanner.render_source_block(str(project), ["../outside.py"])

    assert "outside.py" not in rendered
    assert reads == []


def test_endpoint_and_model_extractors_do_not_read_outside_paths(scanner, tmp_path, monkeypatch):
    """Catches direct helper callers bypassing scan discovery confinement."""
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "api.py"
    outside.write_text("@GetMapping('/secret')", encoding="utf-8")
    reads = []
    monkeypatch.setattr(scanner, "_read_text", lambda path: reads.append(path) or "")

    assert scanner._extract_endpoints(str(project), ["../api.py"], "spring-boot") == []
    assert scanner._extract_models(str(project), ["../api.py"], "python") == []
    assert reads == []
