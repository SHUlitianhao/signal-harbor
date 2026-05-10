#!/usr/bin/env python3
from __future__ import annotations

import compileall
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.environ["PYTHONPATH"] = str(BACKEND)


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    compiled = compileall.compile_dir(str(BACKEND), quiet=1) and compileall.compile_dir(str(ROOT / "scripts"), quiet=1)
    return 0 if result.wasSuccessful() and compiled else 1


if __name__ == "__main__":
    raise SystemExit(main())
