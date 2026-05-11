from __future__ import annotations

import base64
import hmac
import json
import mimetypes
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from signal_harbor.config import AppConfig
from signal_harbor.domain import AlertRule, Collection, Favorite, SavedSearch, Source, Watchlist
from signal_harbor.quant import compute_industry_domain_detail, compute_industry_domains, load_industry_domain_catalog
from signal_harbor.runtime import PublicIngestRuntime
from signal_harbor.storage import SQLiteStore
from signal_harbor.translation import load_translation_provider
from signal_harbor.translation.language import infer_language, is_chinese_language


@dataclass(frozen=True)
class BasicAuthConfig:
    enabled: bool = False
    username: str = "signal-harbor"
    password: str = ""
    scheme: str = "basic"


class SignalHarborServer(HTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        store: SQLiteStore,
        frontend_dir: Path,
        auth_config: BasicAuthConfig | None = None,
        app_config: AppConfig | None = None,
        runtime_manager: PublicIngestRuntime | None = None,
    ) -> None:
        super().__init__(server_address, SignalHarborHandler)
        self.store = store
        self.frontend_dir = frontend_dir
        self.auth_config = auth_config or BasicAuthConfig()
        self.app_config = app_config
        self.runtime_manager = runtime_manager


class SignalHarborHandler(BaseHTTPRequestHandler):
    server: SignalHarborServer

    def do_GET(self) -> None:
        if not self._authorize_request():
            return
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle_api_get(parsed.path, parse_qs(parsed.query))
            return
        self._serve_static(parsed.path)

    def do_POST(self) -> None:
        if not self._authorize_request():
            return
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        payload = self._read_json()
        self._handle_api_post(parsed.path, payload)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _authorize_request(self) -> bool:
        config = self.server.auth_config
        if not config.enabled:
            return True
        expected = f"{config.username}:{config.password}"
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            self._auth_failed()
            return False
        try:
            decoded = base64.b64decode(header.removeprefix("Basic ").strip()).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            self._auth_failed()
            return False
        if not hmac.compare_digest(decoded, expected):
            self._auth_failed()
            return False
        return True

    def _auth_failed(self) -> None:
        if self.path.startswith("/api/"):
            body = json.dumps(
                {"error": "认证失败，请检查远程访问账号或密码。"},
                ensure_ascii=False,
            ).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            body = "认证失败，请检查远程访问账号或密码。".encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Signal Harbor"')
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        store = self.server.store
        if path == "/api/health":
            self._json(store.health())
        elif path == "/api/runtime/status":
            self._json(self._runtime_status())
        elif path == "/api/sources":
            self._json({"sources": store.list_sources()})
        elif path == "/api/industry-domains":
            self._json({"domains": self._industry_domains(query)})
        elif path.startswith("/api/industry-domains/"):
            domain_id = unquote(path.removeprefix("/api/industry-domains/").strip("/"))
            domain = self._industry_domain_detail(domain_id, query)
            if not domain:
                self._json({"error": "industry domain not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"domain": domain})
        elif path == "/api/events":
            self._json({"events": store.list_events(limit=self._int_query(query, "limit", 50))})
        elif path.startswith("/api/events/"):
            event_key = unquote(path.removeprefix("/api/events/").strip("/"))
            event = store.get_event(event_key)
            if not event:
                self._json({"error": "event not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"event": event})
        elif path == "/api/items/latest":
            self._json(
                {
                    "items": store.list_latest_items(
                        limit=self._int_query(query, "limit", 50),
                        source_id=self._optional_str_query(query, "source_id"),
                        translation_status=self._optional_str_query(query, "translation_status"),
                    )
                }
            )
        elif path == "/api/items/search":
            favorite = self._bool_query(query, "favorite")
            min_score = self._float_query(query, "min_score")
            self._json(
                {
                    "items": store.search_items(
                        query=self._str_query(query, "query"),
                        source_id=self._optional_str_query(query, "source_id"),
                        tag=self._optional_str_query(query, "tag"),
                        topic=self._optional_str_query(query, "topic"),
                        min_score=min_score,
                        favorite=favorite,
                        published_from=self._optional_str_query(query, "published_from"),
                        published_to=self._optional_str_query(query, "published_to"),
                        translation_status=self._optional_str_query(query, "translation_status"),
                        limit=self._int_query(query, "limit", 100),
                    )
                }
            )
        elif path == "/api/translation/status":
            self._json({"translation": store.translation_status_summary()})
        elif path == "/api/translation/glossary":
            self._json({"terms": store.list_glossary_terms()})
        elif path.startswith("/api/items/"):
            item_id = path.removeprefix("/api/items/")
            item = store.get_item(item_id)
            if not item:
                self._json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
                return
            self._json({"item": item})
        elif path == "/api/favorites":
            self._json({"favorites": store.list_favorites()})
        elif path == "/api/collections":
            self._json({"collections": store.list_collections()})
        elif path == "/api/watchlists":
            self._json({"watchlists": store.list_watchlists()})
        elif path == "/api/saved-searches":
            self._json({"saved_searches": store.list_saved_searches()})
        elif path == "/api/alert-rules":
            self._json({"alert_rules": store.list_alert_rules()})
        elif path == "/api/task-runs":
            self._json({"task_runs": store.list_task_runs(limit=self._int_query(query, "limit", 50))})
        elif path == "/api/notifications":
            self._json({"notifications": store.list_notifications(limit=self._int_query(query, "limit", 20))})
        else:
            self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _handle_api_post(self, path: str, payload: dict[str, Any]) -> None:
        store = self.server.store
        try:
            if path == "/api/tasks/ingest-public":
                self._run_public_ingest_task()
            elif path == "/api/items/translate-batch":
                self._json({"batch": self._translate_batch(store, payload)})
            elif path == "/api/translation/glossary":
                term = store.create_glossary_term(
                    source_term=str(payload["source_term"]),
                    target_term=str(payload["target_term"]),
                    category=str(payload.get("category", "dictionary")),
                    enabled=self._optional_payload_bool(payload, "enabled", True),
                    notes=str(payload.get("notes", "")),
                )
                self._json({"term": term}, HTTPStatus.CREATED)
            elif path.startswith("/api/translation/glossary/") and path.endswith("/delete"):
                term_id = unquote(path.removeprefix("/api/translation/glossary/").removesuffix("/delete").strip("/"))
                if not store.delete_glossary_term(term_id):
                    self._json({"error": "glossary term not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"deleted": True})
            elif path.startswith("/api/translation/glossary/"):
                term_id = unquote(path.removeprefix("/api/translation/glossary/").strip("/"))
                term = store.update_glossary_term(term_id, payload)
                if not term:
                    self._json({"error": "glossary term not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"term": term})
            elif path.startswith("/api/items/") and path.endswith("/translate"):
                item_id = unquote(path.removeprefix("/api/items/").removesuffix("/translate").strip("/"))
                item = self._translate_item(store, item_id)
                if not item:
                    self._json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"item": item, "translation": item.get("translation", {})})
            elif path == "/api/sources":
                metadata = dict(payload.get("metadata", {}))
                for key in (
                    "description",
                    "publisher",
                    "region",
                    "market",
                    "language",
                    "quality_tier",
                    "rsshub_base_url",
                    "rsshub_route",
                    "rsshub_url",
                    "rsshub_healthcheck_path",
                    "rsshub_check_health",
                    "rsshub_instance_name",
                    "include_keywords",
                    "exclude_keywords",
                    "default_topics",
                    "json_mapping",
                    "html_mapping",
                ):
                    if key in payload:
                        metadata[key] = payload[key]
                metadata["created_via_api"] = True
                source = Source(
                    name=str(payload["name"]),
                    source_type=str(payload.get("source_type", "manual")),
                    location=str(payload.get("location", "")),
                    tags=list(payload.get("tags", [])),
                    enabled=bool(payload.get("enabled", True)),
                    fetch_interval_minutes=int(payload.get("fetch_interval_minutes", 60)),
                    metadata=metadata,
                )
                saved = store.save_source(source)
                self._json({"source": store.get_source(saved.id) or saved.to_dict()}, HTTPStatus.CREATED)
            elif path.startswith("/api/sources/") and path.endswith("/toggle"):
                source_id = unquote(path.removeprefix("/api/sources/").removesuffix("/toggle").strip("/"))
                enabled = self._payload_bool(payload, "enabled")
                source = store.set_source_enabled(source_id, enabled)
                if not source:
                    self._json({"error": "source not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"source": source})
            elif path == "/api/favorites":
                favorite = Favorite(item_id=str(payload["item_id"]), note=str(payload.get("note", "")))
                self._json({"favorite": store.add_favorite(favorite).to_dict()}, HTTPStatus.CREATED)
            elif path == "/api/collections":
                collection = Collection(
                    name=str(payload["name"]),
                    description=str(payload.get("description", "")),
                    item_ids=list(payload.get("item_ids", [])),
                )
                self._json({"collection": store.create_collection(collection).to_dict()}, HTTPStatus.CREATED)
            elif path.startswith("/api/collections/") and path.endswith("/items"):
                collection_id = unquote(path.removeprefix("/api/collections/").removesuffix("/items").strip("/"))
                item_id = str(payload["item_id"])
                if not store.get_item(item_id):
                    self._json({"error": "item not found"}, HTTPStatus.NOT_FOUND)
                    return
                collection = store.append_item_to_collection(collection_id, item_id)
                if not collection:
                    self._json({"error": "collection not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"collection": collection})
            elif path == "/api/watchlists":
                watchlist = Watchlist(
                    name=str(payload["name"]),
                    keywords=list(payload.get("keywords", [])),
                    enabled=bool(payload.get("enabled", True)),
                )
                self._json({"watchlist": store.create_watchlist(watchlist).to_dict()}, HTTPStatus.CREATED)
            elif path == "/api/saved-searches":
                saved_search = SavedSearch(name=str(payload["name"]), query=dict(payload.get("query", {})))
                self._json({"saved_search": store.create_saved_search(saved_search).to_dict()}, HTTPStatus.CREATED)
            elif path == "/api/alert-rules":
                alert_rule = AlertRule(
                    name=str(payload["name"]),
                    keywords=list(payload.get("keywords", [])),
                    min_score=float(payload.get("min_score", 60)),
                    enabled=bool(payload.get("enabled", True)),
                )
                self._json({"alert_rule": store.create_alert_rule(alert_rule).to_dict()}, HTTPStatus.CREATED)
            elif path.startswith("/api/alert-rules/") and path.endswith("/toggle"):
                alert_rule_id = unquote(path.removeprefix("/api/alert-rules/").removesuffix("/toggle").strip("/"))
                enabled = self._payload_bool(payload, "enabled")
                alert_rule = store.set_alert_rule_enabled(alert_rule_id, enabled)
                if not alert_rule:
                    self._json({"error": "alert rule not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._json({"alert_rule": alert_rule})
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (KeyError, TypeError, ValueError) as exc:
            self._json({"error": f"bad request: {exc}"}, HTTPStatus.BAD_REQUEST)

    def _runtime_status(self) -> dict[str, Any]:
        config = self.server.app_config
        health = dict(self.server.store.health())
        health.pop("database", None)
        host = config.host if config else self.server.server_address[0]
        port = config.port if config else self.server.server_port
        actual_port = self.server.server_port if int(port) == 0 else port
        public_base_url = config.remote_public_base_url if config else ""
        scheduler = (
            self.server.runtime_manager.status()
            if self.server.runtime_manager
            else {
                "enabled": False,
                "running": False,
                "interval_minutes": 0,
                "last_started_at": "",
                "last_finished_at": "",
                "last_status": "unavailable",
                "last_error": "",
                "last_reason": "",
                "last_run_ids": [],
            }
        )
        return {
            "health": health,
            "host": host,
            "port": actual_port,
            "local_url": f"http://{host}:{actual_port}",
            "remote_access_enabled": bool(config.remote_access_enabled) if config else self.server.auth_config.enabled,
            "auth_enabled": self.server.auth_config.enabled,
            "auth_scheme": self.server.auth_config.scheme if self.server.auth_config.enabled else "",
            "public_base_url": public_base_url,
            "current_access_url": public_base_url or f"http://{self.headers.get('Host', f'{host}:{actual_port}')}",
            "ingest_on_startup": bool(config.ingest_on_startup) if config else False,
            "public_sources_configured": bool(config.public_sources_config_path) if config else False,
            "database_configured": bool(config.database_path) if config else bool(self.server.store.database_path),
            "scheduler": scheduler,
            "recent_task_runs": [self._task_run_summary(run) for run in self.server.store.list_task_runs()[:8]],
        }

    def _task_run_summary(self, run: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": run.get("id", ""),
            "source_id": run.get("source_id", ""),
            "task_type": run.get("task_type", ""),
            "status": run.get("status", ""),
            "started_at": run.get("started_at", ""),
            "finished_at": run.get("finished_at", ""),
            "items_found": run.get("items_found", 0),
            "items_created": run.get("items_created", 0),
            "items_filtered": run.get("items_filtered", 0),
            "error": run.get("error", ""),
        }

    def _industry_domains(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        catalog = load_industry_domain_catalog()
        items = self.server.store.list_industry_domain_candidate_items()
        return compute_industry_domains(
            items,
            catalog,
            window_days=self._int_query(query, "window_days", 7),
            limit=self._int_query(query, "limit", 5),
        )

    def _industry_domain_detail(self, domain_id: str, query: dict[str, list[str]]) -> dict[str, Any] | None:
        catalog = load_industry_domain_catalog()
        items = self.server.store.list_industry_domain_candidate_items()
        return compute_industry_domain_detail(
            domain_id,
            items,
            catalog,
            window_days=self._int_query(query, "window_days", 7),
        )

    def _run_public_ingest_task(self) -> None:
        runtime = self.server.runtime_manager
        if not runtime:
            self._json({"error": "public ingest runtime not configured"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        result = runtime.run_once("manual")
        self._json({"ingest": result})

    def _translate_item(self, store: SQLiteStore, item_id: str) -> dict[str, Any] | None:
        provider = load_translation_provider(user_terms=store.list_glossary_terms())
        return self._translate_item_with_provider(store, item_id, provider)

    def _translate_item_with_provider(self, store: SQLiteStore, item_id: str, provider: Any) -> dict[str, Any] | None:
        item = store.get_item_model(item_id)
        detail = store.get_item(item_id)
        if not item or not detail:
            return None
        insight = detail.get("insight") or {}
        summary = str(insight.get("summary") or item.canonical_text)
        risk_flags = list(insight.get("risk_flags") or [])
        source_language = infer_language(
            item.title,
            item.canonical_text,
            item.metadata.get("language") or item.metadata.get("lang") or item.lang,
        )
        try:
            translation = provider.translate(item, summary, item.tags, risk_flags)
            if not isinstance(translation, dict):
                translation = {}
        except Exception as exc:
            translation = {
                "status": "error",
                "provider": getattr(provider, "name", "unknown"),
                "source_language": source_language,
                "target_language": "zh",
                "error": str(exc),
            }
        if not translation:
            provider_name = getattr(provider, "name", "unknown")
            translation = {
                "status": (
                    "not_required"
                    if is_chinese_language(source_language)
                    else "disabled"
                    if provider_name == "disabled"
                    else "untranslated"
                ),
                "provider": provider_name,
                "source_language": source_language,
                "target_language": "zh",
            }
        translation.setdefault("provider", getattr(provider, "name", "unknown"))
        translation.setdefault("source_language", source_language)
        translation.setdefault("target_language", "zh")
        return store.apply_item_translation(item_id, translation)

    def _translate_batch(self, store: SQLiteStore, payload: dict[str, Any]) -> dict[str, Any]:
        limit = self._clean_payload_limit(payload.get("limit", 50))
        source_id = str(payload.get("source_id", "") or "").strip() or None
        status = str(payload.get("status") or payload.get("translation_status") or "untranslated")
        items = store.list_items_for_translation(source_id=source_id, translation_status=status, limit=limit)
        provider = load_translation_provider(user_terms=store.list_glossary_terms())
        result = {
            "requested": len(items),
            "translated": 0,
            "failed": 0,
            "status_filter": status,
            "source_id": source_id or "",
            "items": [],
        }
        for item in items:
            try:
                translated_item = self._translate_item_with_provider(store, item["id"], provider)
                if not translated_item:
                    raise ValueError("item not found")
                translation = translated_item.get("translation", {})
                item_status = str(translation.get("status", "missing"))
                if item_status == "error":
                    result["failed"] += 1
                else:
                    result["translated"] += 1
                result["items"].append(
                    {
                        "id": translated_item["id"],
                        "title": translated_item["title"],
                        "translation_status": item_status,
                    }
                )
            except Exception as exc:
                result["failed"] += 1
                result["items"].append(
                    {
                        "id": item.get("id", ""),
                        "title": item.get("title", ""),
                        "translation_status": "error",
                        "error": str(exc),
                    }
                )
        return result

    def _serve_static(self, request_path: str) -> None:
        frontend_dir = self.server.frontend_dir
        relative = request_path.lstrip("/") or "index.html"
        candidate = (frontend_dir / relative).resolve()
        root = frontend_dir.resolve()
        if not str(candidate).startswith(str(root)) or not candidate.exists() or candidate.is_dir():
            candidate = root / "index.html"
        content = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _str_query(self, query: dict[str, list[str]], key: str, default: str = "") -> str:
        return query.get(key, [default])[0]

    def _optional_str_query(self, query: dict[str, list[str]], key: str) -> str | None:
        value = self._str_query(query, key)
        return value or None

    def _int_query(self, query: dict[str, list[str]], key: str, default: int) -> int:
        try:
            return int(self._str_query(query, key, str(default)))
        except ValueError:
            return default

    def _float_query(self, query: dict[str, list[str]], key: str) -> float | None:
        value = self._str_query(query, key)
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _bool_query(self, query: dict[str, list[str]], key: str) -> bool | None:
        value = self._str_query(query, key).lower()
        if value in {"1", "true", "yes"}:
            return True
        if value in {"0", "false", "no"}:
            return False
        return None

    def _payload_bool(self, payload: dict[str, Any], key: str) -> bool:
        if key not in payload:
            raise KeyError(key)
        value = payload[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in {"1", "true", "yes"}:
                return True
            if normalized in {"0", "false", "no"}:
                return False
        if isinstance(value, int):
            return bool(value)
        raise ValueError(f"{key} must be boolean")

    def _optional_payload_bool(self, payload: dict[str, Any], key: str, default: bool) -> bool:
        if key not in payload:
            return default
        return self._payload_bool(payload, key)

    def _clean_payload_limit(self, value: Any) -> int:
        try:
            return max(1, min(int(value), 200))
        except (TypeError, ValueError):
            return 50


def create_server(
    host: str,
    port: int,
    store: SQLiteStore,
    frontend_dir: str | Path,
    auth_config: BasicAuthConfig | None = None,
    app_config: AppConfig | None = None,
    runtime_manager: PublicIngestRuntime | None = None,
) -> SignalHarborServer:
    return SignalHarborServer(
        (host, port),
        store=store,
        frontend_dir=Path(frontend_dir),
        auth_config=auth_config,
        app_config=app_config,
        runtime_manager=runtime_manager,
    )
