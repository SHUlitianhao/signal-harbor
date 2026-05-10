#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.config import load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理 Signal Harbor 运行日志数据，默认只预览不删除。")
    parser.add_argument("--database", help="SQLite 数据库路径；缺省读取应用配置。")
    parser.add_argument("--days", type=int, default=90, help="保留最近 N 天的任务记录和站内消息。")
    parser.add_argument("--execute", action="store_true", help="实际执行删除；不加此参数时为 dry-run。")
    args = parser.parse_args(argv)

    if args.days <= 0:
        parser.error("--days must be greater than 0")

    database_path = _resolve_database_path(args.database)
    if not database_path.exists():
        print(json.dumps({"error": f"database not found: {database_path}"}, ensure_ascii=False), file=sys.stderr)
        return 1

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    result = cleanup_database(database_path, cutoff, execute=args.execute)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def cleanup_database(database_path: Path, cutoff: datetime, execute: bool = False) -> dict[str, object]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        task_ids = _older_ids(connection, "task_runs", "id", "started_at", cutoff)
        notification_ids = _older_ids(connection, "notifications", "id", "created_at", cutoff)
        if execute:
            _delete_ids(connection, "task_runs", task_ids)
            _delete_ids(connection, "notifications", notification_ids)
            connection.commit()
        return {
            "database": str(database_path),
            "dry_run": not execute,
            "cutoff": cutoff.isoformat(),
            "task_runs": len(task_ids),
            "notifications": len(notification_ids),
        }
    finally:
        connection.close()


def _resolve_database_path(value: str | None) -> Path:
    if value:
        path = Path(value).expanduser()
        return path if path.is_absolute() else ROOT / path
    return load_config().database_path


def _older_ids(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    time_column: str,
    cutoff: datetime,
) -> list[str]:
    rows = connection.execute(f"SELECT {id_column}, {time_column} FROM {table}").fetchall()
    ids: list[str] = []
    for row in rows:
        parsed = _parse_time(str(row[time_column] or ""))
        if parsed and parsed < cutoff:
            ids.append(str(row[id_column]))
    return ids


def _delete_ids(connection: sqlite3.Connection, table: str, ids: list[str]) -> None:
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    connection.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)


def _parse_time(value: str) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":
    raise SystemExit(main())
