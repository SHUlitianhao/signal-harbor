#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.config import load_config
from signal_harbor.runtime import ensure_runtime_defaults, record_public_ingest_failure, run_public_ingest
from signal_harbor.storage import SQLiteStore


def _resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return ROOT / path


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    app_config = load_config()
    sources_config = _resolve_path(args[0]) if args else app_config.public_sources_config_path

    store = SQLiteStore(app_config.database_path)
    try:
        ensure_runtime_defaults(store)
        runs = run_public_ingest(store, sources_config, app_config.translation_config_path)
        for run in runs:
            print(
                f"public ingest {run.status}: source={run.source_id} "
                f"found={run.items_found} created={run.items_created} error={run.error or '-'}"
            )
        failed = [run for run in runs if run.status != "success"]
        return 1 if failed else 0
    except Exception as exc:
        run = record_public_ingest_failure(store, sources_config, "cli", str(exc))
        print(
            f"public ingest {run.status}: source={run.source_id} "
            f"found={run.items_found} created={run.items_created} error={run.error or '-'}"
        )
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
