"""The one form of a moment the pack writes: UTC with microseconds and ``Z`` (``2026-11-26T21:00:00.000000Z``).

The event journal (``observed_at``), ``refusals.jsonl`` and the step-size records write it; the journal and the report read it.
A leaf: only ``datetime``, so the lightest command may take it."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_z() -> str:
    """Now, in UTC, to the microsecond, with ``Z``."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_z(stamp: str) -> datetime:
    """A moment written with ``Z`` (or an offset) as an aware ``datetime``; ``ValueError`` when it is none."""
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
