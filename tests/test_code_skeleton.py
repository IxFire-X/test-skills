"""v0.8 (rnd/20, proposal 2): the generator reads the files it needs whole and the other code files of the context as their skeleton —
declarations with line numbers (v08/skeleton-context-plan.md)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_batch_plan import _evidence, _requirements
from tests.test_marker_parts import _run
from tools.batch_assembly import pick_context, plan_batches
from tools.code_skeleton import LINE_LIMIT, file_parts, generated, skeleton, skeleton_files, view_bytes
from tools.step_size import _skeleton

OWNER = """package org.example.owner;

import java.util.List;

@Entity
public class Owner {
    @OrderBy("name")
    private List<Pet> pets;

    String nickname(int length) {
        return "x".repeat(length);
    }

    void touch() {
        if (pets.isEmpty()) {
            return;
        }
    }
}
"""


def test_the_skeleton_keeps_declarations_with_their_line_numbers() -> None:
    assert [number for number, _line in skeleton(OWNER, ".java")] == [1, 5, 6, 7, 8, 10, 14]


def test_an_annotation_over_a_field_is_in_the_skeleton() -> None:  # the defect D03 of the ruler PQ1: @OrderBy in Pet.java
    assert (7, '@OrderBy("name")') in skeleton(OWNER, ".java")


KOTLIN = """package com.example.topic

@HiltViewModel
class TopicViewModel @Inject constructor(
    private val repo: TopicsRepository,
) : ViewModel() {
    val uiState: StateFlow<TopicUiState> = repo.topic()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), TopicUiState.Loading)

    fun followTopicToggle(followed: Boolean) {
        viewModelScope.launch {
            repo.setTopicIdFollowed(topicId, "{ not a brace }")
        }
    }

    companion object {
        const val TOPIC_ID = "topicId"
    }
}

data class Topic(val id: String)

fun topicRoute(id: String) {
    println(id)
}
"""

SWIFT = """import UIKit

final class ComposeViewModel: ObservableObject {
    @Published var text = ""
    var count: Int {
        text.count
    }
    let handler = { (value: Int) in
        print(value)
    }
    class func make() -> ComposeViewModel {
        return ComposeViewModel()
    }
    func publish(
        visibility: Visibility
    ) async throws -> Status {
        let status = try await api.publish(text)
        return status
    }
}
extension ComposeViewModel {
    func reset() { text = "" }
}
"""


def test_a_kotlin_type_with_its_constructor_over_lines_keeps_its_members() -> None:
    assert [number for number, _line in skeleton(KOTLIN, ".kt")] == [1, 3, 4, 5, 7, 10, 16, 17, 21, 23]


def test_no_line_of_a_swift_body_leaks_into_the_skeleton() -> None:  # the first reading of R&D-20c leaked 248 lines of bodies on mastodon-ios
    assert [number for number, _line in skeleton(SWIFT, ".swift")] == [3, 4, 5, 8, 11, 14, 21, 22]


def test_a_line_over_the_limit_is_no_declaration_but_its_braces_count() -> None:
    code = "public class A {\n    void a() {\n        String s = \"" + "x" * LINE_LIMIT + "\"; }\n    void b() {\n        call();\n    }\n}\n"
    assert [number for number, _line in skeleton(code, ".java")] == [1, 2, 4]

def test_python_gives_the_top_level_and_the_members_of_a_class() -> None:
    code = "import os\n\n@dataclass\nclass Owner:\n    name: str\n\n    def greet(self):\n        def inner():\n            pass\n\ndef main():\n    pass\n"
    assert [number for number, _line in skeleton(code, ".py")] == [3, 4, 7, 11]


def test_generated_code_is_no_part_of_the_skeleton() -> None:
    assert (generated("MastodonSDK/Sources/MastodonLocalization/Generated/Strings.swift"), generated("Sources/L10n.swift", "// swiftlint:disable all\n// Generated using SwiftGen"),
            generated("Sources/Compose.swift", "import UIKit\n")) == (True, True, False)


def test_a_code_file_of_a_language_without_a_reading_is_its_path_and_size(tmp_path: Path) -> None:
    row = _file(tmp_path, "cmd/main.go", "package main\n\nfunc main() {}\n")
    assert file_parts(tmp_path, [row]) == {"cmd/main.go": f"## cmd/main.go ({row['size']} Б)\n"}


def _file(project: Path, relative: str, text: str) -> dict:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return {"project_path": relative, "size": path.stat().st_size, "content_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


DOC = "# Visits\n\nA visit has a date.\n"


def _pick(tmp_path: Path, room: int | None = None) -> tuple[list[str], list[str], int, int | None] | None:
    rows = [_file(tmp_path, "src/Owner.java", OWNER), _file(tmp_path, "src/Visit.java", OWNER.replace("Owner", "Visit")),
            _file(tmp_path, "docs/visits.md", DOC)]
    receipt = {"files": rows, "byte_count": room if room is not None else 10_000}
    return pick_context(tmp_path, [receipt], 1, [{"provenance": ["docs/visits.md:1 — R1"]}], ["src/Visit.java:3 — a visit"])


def test_a_named_file_is_read_whole_and_another_code_file_goes_as_its_skeleton(tmp_path: Path) -> None:
    chosen, _missed, _total, skeleton = _pick(tmp_path)
    assert (chosen, skeleton > 0) == (["docs/visits.md", "src/Visit.java"], True)


def test_a_skeleton_that_does_not_fit_beside_the_files_needed_is_left_out(tmp_path: Path) -> None:  # the plan says context_skeleton: false
    owner = view_bytes(tmp_path, [_file(tmp_path, "src/Owner.java", OWNER)])
    room = len(owner) - 1 + len(OWNER.replace("Owner", "Visit").encode("utf-8")) + len(DOC.encode("utf-8"))
    chosen, missed, _total, skeleton = _pick(tmp_path, room)
    assert (chosen, missed, skeleton) == (["docs/visits.md", "src/Visit.java"], [], None)


def test_a_named_file_with_a_blank_in_its_path_is_read_whole(tmp_path: Path) -> None:  # mastodon-ios: 101 of 614 Swift files
    rows = [_file(tmp_path, "App/Notification Filtering/Policy.swift", "struct Policy {}\n"), _file(tmp_path, "App/Other.swift", "struct Other {}\n")]
    picked = pick_context(tmp_path, [{"files": rows, "byte_count": 10_000}], 1, [], ["App/Notification Filtering/Policy.swift:1 — a policy"])
    assert picked[0] == ["App/Notification Filtering/Policy.swift"]


def test_documents_and_templates_are_no_part_of_the_skeleton() -> None:
    receipt = {"files": [{"project_path": path} for path in ("README.md", "templates/owners.html", "src/Owner.java", "app.yml")]}
    assert [row["project_path"] for row in skeleton_files([receipt], [])] == ["src/Owner.java"]


@pytest.mark.parametrize("with_skeleton", [True, False])
def test_every_new_plan_says_whether_it_has_the_skeleton(with_skeleton: bool) -> None:
    assert plan_batches(_requirements(), _evidence(), context_skeleton=with_skeleton)["context_skeleton"] is with_skeleton


def _run_with_code(tmp_path: Path) -> tuple[Path, str, dict]:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "Owner.java").write_text(OWNER, encoding="utf-8")  # in the project before its scan
    root, attempt_id, _baseline, receipts = _run(tmp_path, files=1)
    return root, attempt_id, {"context_skeleton": True, "context_files": ["src/part0.py"], "context_receipt_digest": receipts[0]["digest"]}


def test_a_plan_with_the_skeleton_gives_the_generator_its_view(tmp_path: Path) -> None:
    root, attempt_id, plan = _run_with_code(tmp_path)
    row = _skeleton(tmp_path, root, attempt_id, plan, "sha256:" + "a" * 64, True)
    assert row["basis"] == "скелет" and "## src/Owner.java\n1: package org.example.owner;\n" in Path(row["path"]).read_text(encoding="utf-8")


def test_a_plan_before_the_skeleton_reads_as_it_did(tmp_path: Path) -> None:
    root, attempt_id, plan = _run_with_code(tmp_path)
    assert _skeleton(tmp_path, root, attempt_id, {key: value for key, value in plan.items() if key != "context_skeleton"}, "sha256:" + "a" * 64, True) is None


def test_the_report_counts_the_skeleton_only_when_its_file_is_there(tmp_path: Path) -> None:
    root, attempt_id, plan = _run_with_code(tmp_path)
    assert _skeleton(tmp_path, root, attempt_id, plan, "sha256:" + "a" * 64, False) is None  # an old run: prepare wrote no view
    written = _skeleton(tmp_path, root, attempt_id, plan, "sha256:" + "a" * 64, True)
    assert _skeleton(tmp_path, root, attempt_id, plan, "sha256:" + "a" * 64, False) == written
