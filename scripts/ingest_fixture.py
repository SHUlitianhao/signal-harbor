#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import FixtureSourceAdapter
from signal_harbor.config import load_config
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import AlertRule, Watchlist
from signal_harbor.storage import SQLiteStore
from signal_harbor.translation import load_translation_provider


def main() -> int:
    config = load_config()
    store = SQLiteStore(config.database_path)
    try:
        if not store.list_watchlists():
            store.create_watchlist(Watchlist(name="默认观察清单", keywords=["AI", "芯片", "监管", "风险"]))
        if not store.list_alert_rules():
            store.create_alert_rule(AlertRule(name="高价值事件提醒", keywords=["风险", "监管", "制裁"], min_score=60))
        pipeline = IngestPipeline(store, translation_provider=load_translation_provider(config.translation_config_path))
        run = pipeline.run_adapter(FixtureSourceAdapter(config.fixture_path))
        print(
            f"fixture ingest {run.status}: found={run.items_found} created={run.items_created} error={run.error or '-'}"
        )
        return 0 if run.status == "success" else 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
