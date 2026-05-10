#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import FixtureSourceAdapter
from signal_harbor.api import BasicAuthConfig, create_server
from signal_harbor.config import AppConfig, load_config
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import AlertRule, Watchlist
from signal_harbor.runtime import PublicIngestRuntime
from signal_harbor.storage import SQLiteStore
from signal_harbor.translation import load_translation_provider


def seed_if_empty(store: SQLiteStore, fixture_path: Path, translation_config_path: Path) -> None:
    if not store.list_watchlists():
        store.create_watchlist(Watchlist(name="默认观察清单", keywords=["AI", "芯片", "监管", "风险"]))
    if not store.list_alert_rules():
        store.create_alert_rule(AlertRule(name="高价值事件提醒", keywords=["风险", "监管", "制裁"], min_score=60))
    if store.health()["items"] == 0:
        IngestPipeline(store, translation_provider=load_translation_provider(translation_config_path)).run_adapter(
            FixtureSourceAdapter(fixture_path)
        )


def validate_remote_access(config: AppConfig) -> tuple[bool, str]:
    if config.remote_access_enabled:
        if config.remote_auth_scheme != "basic":
            return False, "远程访问模式当前只支持 Basic Auth。"
        if not config.remote_auth_password:
            return False, "远程访问模式已启用，但缺少 SIGNAL_HARBOR_REMOTE_PASSWORD。"
    elif config.host not in {"127.0.0.1", "localhost", "::1"}:
        return True, "警告：当前监听地址仅限受信任局域网使用，不要直接公网暴露。"
    return True, ""


def main() -> int:
    config = load_config()
    valid, message = validate_remote_access(config)
    if message:
        print(message, file=sys.stderr if not valid else sys.stdout)
    if not valid:
        return 2
    store = SQLiteStore(config.database_path)
    seed_if_empty(store, config.fixture_path, config.translation_config_path)
    runtime = PublicIngestRuntime(
        database_path=config.database_path,
        sources_config_path=config.public_sources_config_path,
        translation_config_path=config.translation_config_path,
        ingest_interval_minutes=config.ingest_interval_minutes,
        status_callback=print,
    )
    if config.ingest_on_startup:
        runtime.run_once("startup")
    runtime.start_scheduler()
    if config.ingest_interval_minutes > 0:
        print(f"Public source scheduler enabled: every {config.ingest_interval_minutes} minutes")
    server = create_server(
        config.host,
        config.port,
        store,
        config.frontend_dir,
        auth_config=BasicAuthConfig(
            enabled=config.remote_access_enabled,
            username=config.remote_auth_username,
            password=config.remote_auth_password,
            scheme=config.remote_auth_scheme,
        ),
        app_config=config,
        runtime_manager=runtime,
    )
    print(f"Signal Harbor listening on http://{config.host}:{config.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nSignal Harbor stopped")
    finally:
        runtime.stop()
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
