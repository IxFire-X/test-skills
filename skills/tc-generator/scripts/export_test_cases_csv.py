#!/usr/bin/env python3
"""Compatibility entry point for the V3 three-artifact bundle publisher."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.publish_test_case_bundle import main


if __name__ == "__main__":
    raise SystemExit(main())
