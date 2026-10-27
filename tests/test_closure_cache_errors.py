"""FaT-9b fix: an immutable error raised inside closure_cache keeps its type, code and diagnostics."""
from __future__ import annotations

import pytest

from tools.build_trace_document import TraceBuildError
from tools.orchestrate_test_case_revision import OrchestrationError
from tools.pilot_state import closure_cache


@pytest.mark.parametrize("error", [
    OrchestrationError("ORCHESTRATION_SELECTION", [{"path": "/x", "code": "EDIT_OLD_MISMATCH", "message": "m"}]),
    TraceBuildError([{"path": "/x", "code": "TRACE_IO", "message": "m"}]),
])
def test_the_error_leaves_the_closure_cache_as_itself(error: ValueError) -> None:
    with pytest.raises(type(error)) as caught:
        with closure_cache():
            raise error from None
    assert caught.value is error and caught.value.__traceback__ is not None
    with pytest.raises(AttributeError):
        error.diagnostics = ()
