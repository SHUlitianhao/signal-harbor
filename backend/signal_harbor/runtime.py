from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from signal_harbor.adapters import build_public_adapters, load_public_source_configs, public_source_configs_from_sources
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import AlertRule, Source, TaskRun, Watchlist, now_iso
from signal_harbor.storage import SQLiteStore
from signal_harbor.translation import load_translation_provider


RUNTIME_PUBLIC_SOURCE_ID = "src_runtime_public_ingest"


def ensure_runtime_defaults(store: SQLiteStore) -> None:
    if not store.list_watchlists():
        store.create_watchlist(Watchlist(name="默认观察清单", keywords=["AI", "芯片", "监管", "风险"]))
    if not store.list_alert_rules():
        store.create_alert_rule(AlertRule(name="高价值事件提醒", keywords=["风险", "监管", "制裁"], min_score=60))


def run_public_ingest(
    store: SQLiteStore,
    sources_config_path: str | Path,
    translation_config_path: str | Path,
) -> list[TaskRun]:
    configs = load_public_source_configs(str(sources_config_path))
    config_source_ids = {config.to_source().id for config in configs}
    configs.extend(public_source_configs_from_sources(store.list_sources(), excluded_ids=config_source_ids))
    adapters = build_public_adapters(configs)
    runnable_adapters = []
    for adapter in adapters:
        existing_source = store.get_source(adapter.source.id)
        if existing_source and not existing_source["enabled"]:
            continue
        runnable_adapters.append(adapter)

    provider = load_translation_provider(translation_config_path, user_terms=store.list_glossary_terms())
    return IngestPipeline(store, translation_provider=provider).run_adapters(runnable_adapters)


def record_public_ingest_failure(
    store: SQLiteStore,
    sources_config_path: str | Path,
    reason: str,
    error: str,
    started_at: str | None = None,
) -> TaskRun:
    source = Source(
        id=RUNTIME_PUBLIC_SOURCE_ID,
        name="公开源采集运行时",
        source_type="runtime",
        location=str(sources_config_path),
        tags=["runtime"],
        enabled=True,
        fetch_interval_minutes=0,
        metadata={"description": "公开源采集入口级失败记录"},
    )
    store.save_source(source)
    task_run = TaskRun(
        source_id=source.id,
        task_type="ingest-public",
        status="failed",
        started_at=started_at or now_iso(),
        finished_at=now_iso(),
        error=error,
        metadata={"reason": reason, "sources_config_path": str(sources_config_path)},
    )
    store.add_task_run(task_run)
    store.update_source_status(source.id, success=False, error=error)
    return task_run


class PublicIngestRuntime:
    def __init__(
        self,
        database_path: str | Path,
        sources_config_path: str | Path,
        translation_config_path: str | Path,
        ingest_interval_minutes: int = 0,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.sources_config_path = Path(sources_config_path)
        self.translation_config_path = Path(translation_config_path)
        self.ingest_interval_minutes = max(0, int(ingest_interval_minutes))
        self.status_callback = status_callback
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._last_started_at = ""
        self._last_finished_at = ""
        self._last_status = "idle"
        self._last_error = ""
        self._last_reason = ""
        self._last_run_ids: list[str] = []

    def start_scheduler(self) -> None:
        if self.ingest_interval_minutes <= 0 or self._thread:
            return
        self._thread = threading.Thread(target=self._scheduler_loop, name="signal-harbor-public-ingest", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "enabled": self.ingest_interval_minutes > 0,
                "running": self._lock.locked(),
                "interval_minutes": self.ingest_interval_minutes,
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                "last_status": self._last_status,
                "last_error": self._last_error,
                "last_reason": self._last_reason,
                "last_run_ids": list(self._last_run_ids),
            }

    def run_once(self, reason: str = "manual") -> dict[str, Any]:
        if not self._lock.acquire(blocking=False):
            return {
                "status": "running",
                "reason": reason,
                "message": "公开源采集正在运行，请稍后再试。",
                "runs": [],
            }
        try:
            return self._run_locked(reason)
        finally:
            self._lock.release()

    def _scheduler_loop(self) -> None:
        seconds = max(1, self.ingest_interval_minutes) * 60
        while not self._stop_event.wait(seconds):
            self.run_once("scheduled")

    def _run_locked(self, reason: str) -> dict[str, Any]:
        started_at = now_iso()
        self._set_state(
            last_started_at=started_at,
            last_finished_at="",
            last_status="running",
            last_error="",
            last_reason=reason,
            last_run_ids=[],
        )
        store = SQLiteStore(self.database_path)
        try:
            ensure_runtime_defaults(store)
            runs = run_public_ingest(store, self.sources_config_path, self.translation_config_path)
            failed = [run for run in runs if run.status != "success"]
            status = "failed" if failed else "success"
            error = "; ".join(run.error or "" for run in failed if run.error)
            result = self._result(status=status, reason=reason, started_at=started_at, runs=runs, error=error)
            self._emit_result(result)
            return result
        except Exception as exc:
            error = str(exc)
            failure_run = record_public_ingest_failure(store, self.sources_config_path, reason, error, started_at)
            result = self._result(status="failed", reason=reason, started_at=started_at, runs=[failure_run], error=error)
            self._emit_result(result)
            return result
        finally:
            store.close()

    def _result(
        self,
        status: str,
        reason: str,
        started_at: str,
        runs: list[TaskRun],
        error: str = "",
    ) -> dict[str, Any]:
        finished_at = now_iso()
        run_ids = [run.id for run in runs]
        self._set_state(
            last_started_at=started_at,
            last_finished_at=finished_at,
            last_status=status,
            last_error=error,
            last_reason=reason,
            last_run_ids=run_ids,
        )
        return {
            "status": status,
            "reason": reason,
            "started_at": started_at,
            "finished_at": finished_at,
            "items_found": sum(run.items_found for run in runs),
            "items_created": sum(run.items_created for run in runs),
            "items_filtered": sum(run.items_filtered for run in runs),
            "error": error,
            "runs": [run.to_dict() for run in runs],
        }

    def _set_state(self, **changes: Any) -> None:
        with self._state_lock:
            for key, value in changes.items():
                setattr(self, f"_{key}", value)

    def _emit_result(self, result: dict[str, Any]) -> None:
        if not self.status_callback:
            return
        message = (
            f"public ingest {result['status']}: reason={result['reason']} "
            f"found={result['items_found']} created={result['items_created']} "
            f"filtered={result['items_filtered']} error={result.get('error') or '-'}"
        )
        self.status_callback(message)
