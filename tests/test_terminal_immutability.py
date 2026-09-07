from tools.finalize_attempt import finalize_attempt


def test_existing_terminal_is_returned_without_new_callbacks_or_executor(tmp_path):
    calls = []
    existing = {"digest": "sha256:" + "f" * 64, "attempt_state": "TERMINAL"}

    def read(kind, key):
        calls.append(("read", kind, key))
        if kind == "terminal_result":
            return existing
        raise KeyError(key)

    result = finalize_attempt({"attempt_id": "a" * 32}, {"files": []}, project=tmp_path, verification="PASS", facts={}, publish=lambda *_: (_ for _ in ()).throw(AssertionError("publish")), read=read)
    assert result == {"result": existing, "idempotent": True}
    assert calls == [("read", "terminal_result", "a" * 32)]
