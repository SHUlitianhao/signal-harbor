from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    database_path: Path
    fixture_path: Path
    frontend_dir: Path
    translation_config_path: Path
    public_sources_config_path: Path
    ingest_on_startup: bool = False
    ingest_interval_minutes: int = 0
    remote_public_base_url: str = ""
    remote_access_enabled: bool = False
    remote_auth_scheme: str = "basic"
    remote_auth_username: str = "signal-harbor"
    remote_auth_password: str = ""
    host: str = "127.0.0.1"
    port: int = 8765


def _project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def load_config(path: str | Path | None = None) -> AppConfig:
    config_path = path or os.environ.get("SIGNAL_HARBOR_CONFIG")
    raw: dict[str, Any] = {}
    if config_path:
        resolved = _project_path(config_path)
        if resolved.exists():
            raw = json.loads(resolved.read_text(encoding="utf-8"))

    data_dir = _project_path(os.environ.get("SIGNAL_HARBOR_DATA_DIR", raw.get("data_dir", "data")))
    database_path = _project_path(raw.get("database_path", str(data_dir / "signal_harbor.sqlite3")))
    fixture_path = _project_path(raw.get("fixture_path", "config/fixtures/items.json"))
    frontend_dir = _project_path(raw.get("frontend_dir", "frontend/static"))
    translation_config_path = _project_path(
        os.environ.get("SIGNAL_HARBOR_TRANSLATION_CONFIG", raw.get("translation_config_path", "config/translation.example.json"))
    )
    public_sources_config_path = _project_path(
        os.environ.get("SIGNAL_HARBOR_SOURCES_CONFIG", raw.get("public_sources_config_path", "config/sources.example.json"))
    )
    ingest_on_startup = _as_bool(
        os.environ.get("SIGNAL_HARBOR_INGEST_ON_STARTUP", raw.get("ingest_on_startup", False))
    )
    ingest_interval_minutes = _as_int(
        os.environ.get("SIGNAL_HARBOR_INGEST_INTERVAL_MINUTES", raw.get("ingest_interval_minutes", 0)),
        default=0,
    )
    remote_access_enabled = _as_bool(
        os.environ.get("SIGNAL_HARBOR_REMOTE_ACCESS", raw.get("remote_access_enabled", False))
    )
    remote_auth_scheme = str(os.environ.get("SIGNAL_HARBOR_REMOTE_AUTH_SCHEME", raw.get("remote_auth_scheme", "basic"))).lower()
    remote_auth_username = str(
        os.environ.get("SIGNAL_HARBOR_REMOTE_USERNAME", raw.get("remote_auth_username", "signal-harbor"))
    )
    remote_auth_password = os.environ.get("SIGNAL_HARBOR_REMOTE_PASSWORD", "")
    remote_public_base_url = str(
        os.environ.get("SIGNAL_HARBOR_REMOTE_PUBLIC_BASE_URL", raw.get("remote_public_base_url", ""))
    ).strip()
    return AppConfig(
        data_dir=data_dir,
        database_path=database_path,
        fixture_path=fixture_path,
        frontend_dir=frontend_dir,
        translation_config_path=translation_config_path,
        public_sources_config_path=public_sources_config_path,
        ingest_on_startup=ingest_on_startup,
        ingest_interval_minutes=max(0, ingest_interval_minutes),
        remote_public_base_url=remote_public_base_url,
        remote_access_enabled=remote_access_enabled,
        remote_auth_scheme=remote_auth_scheme,
        remote_auth_username=remote_auth_username,
        remote_auth_password=remote_auth_password,
        host=str(raw.get("host", os.environ.get("SIGNAL_HARBOR_HOST", "127.0.0.1"))),
        port=int(raw.get("port", os.environ.get("SIGNAL_HARBOR_PORT", "8765"))),
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
