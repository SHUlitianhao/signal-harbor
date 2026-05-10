from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.storage import SQLiteStore


class StorageRuntimeTest(unittest.TestCase):
    def test_sqlite_connection_uses_busy_timeout_and_wal_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            store = SQLiteStore(Path(tempdir) / "runtime.sqlite3")
            try:
                busy_timeout = store.connection.execute("PRAGMA busy_timeout").fetchone()[0]
                journal_mode = str(store.connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            finally:
                store.close()

        self.assertGreaterEqual(busy_timeout, 30000)
        self.assertIn(journal_mode, {"wal", "delete", "memory"})


if __name__ == "__main__":
    unittest.main()
