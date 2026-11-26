"""The one form of a moment the pack writes (C6, Б5): UTC, microseconds, ``Z``."""

from __future__ import annotations

import re
from datetime import timedelta

from tools.clock import parse_z, utc_now_z


def test_now_is_utc_to_the_microsecond_with_z() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", utc_now_z())


def test_a_written_moment_reads_back_as_utc() -> None:
    assert parse_z(utc_now_z()).utcoffset() == timedelta(0)
