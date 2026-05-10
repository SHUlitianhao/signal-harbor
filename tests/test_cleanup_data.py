from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from scripts.cleanup_data import cleanup_database
from signal_harbor.domain import Notification, Source, TaskRun
from signal_harbor.storage import SQLiteStore


class CleanupDataTest(unittest.TestCase):
    def test_cleanup_dry_run_does_not_delete_and_execute_deletes_old_runtime_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            database_path = Path(tempdir) / "cleanup.sqlite3"
            store = SQLiteStore(database_path)
            try:
                source = store.save_source(Source(id="src_cleanup", name="清理测试源", source_type="rss", location="memory://cleanup"))
                now = datetime.now(timezone.utc).replace(microsecond=0)
                old = (now - timedelta(days=100)).isoformat()
                recent = (now - timedelta(days=1)).isoformat()
                store.add_task_run(TaskRun(source_id=source.id, task_type="ingest", status="success", started_at=old))
                store.add_task_run(TaskRun(source_id=source.id, task_type="ingest", status="success", started_at=recent))
                store.add_notification(Notification(title="旧消息", message="待清理", created_at=old))
                store.add_notification(Notification(title="新消息", message="保留", created_at=recent))
                dry_run = cleanup_database(database_path, now - timedelta(days=30), execute=False)
                self.assertEqual(dry_run["task_runs"], 1)
                self.assertEqual(dry_run["notifications"], 1)
                self.assertEqual(len(store.list_task_runs()), 2)
                self.assertEqual(len(store.list_notifications()), 2)
                executed = cleanup_database(database_path, now - timedelta(days=30), execute=True)
                self.assertEqual(executed["task_runs"], 1)
                self.assertEqual(executed["notifications"], 1)
                self.assertEqual(len(store.list_task_runs()), 1)
                self.assertEqual(len(store.list_notifications()), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
