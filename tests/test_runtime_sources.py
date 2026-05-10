from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.api import create_server
from signal_harbor.config import AppConfig
from signal_harbor.runtime import PublicIngestRuntime
from signal_harbor.storage import SQLiteStore


class RssHubFixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send("ok", "text/plain")
            return
        if self.path == "/rsshub/test":
            self._send(
                """<?xml version="1.0" encoding="UTF-8"?>
                <rss version="2.0">
                  <channel>
                    <item>
                      <title>手机新增 RSSHub 来源</title>
                      <link>https://example.test/mobile-rsshub</link>
                      <description>监管风险和 AI 供应更新。</description>
                      <pubDate>Sat, 09 May 2026 12:00:00 +0800</pubDate>
                    </item>
                  </channel>
                </rss>
                """,
                "application/rss+xml",
            )
            return
        if self.path == "/items.json":
            self._send(json.dumps({"items": [{"title": "未配置 mapping 的 JSON"}]}, ensure_ascii=False), "application/json")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, body: str, content_type: str) -> None:
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class RuntimeSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.frontend_dir = self.root / "frontend"
        self.frontend_dir.mkdir()
        (self.frontend_dir / "index.html").write_text("<h1>Signal Harbor</h1>", encoding="utf-8")
        self.sources_path = self.root / "sources.json"
        self.sources_path.write_text(json.dumps({"sources": []}), encoding="utf-8")
        self.store = SQLiteStore(self.root / "runtime.sqlite3")
        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), RssHubFixtureHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()
        self.upstream_url = f"http://127.0.0.1:{self.upstream.server_port}"

    def tearDown(self) -> None:
        self.upstream.shutdown()
        self.upstream_thread.join(timeout=2)
        self.upstream.server_close()
        self.store.close()
        self.tempdir.cleanup()

    def start_app(self) -> str:
        config = AppConfig(
            data_dir=self.root / "data",
            database_path=self.store.database_path,
            fixture_path=self.root / "items.json",
            frontend_dir=self.frontend_dir,
            translation_config_path=ROOT / "config" / "translation.example.json",
            public_sources_config_path=self.sources_path,
            host="127.0.0.1",
            port=0,
        )
        runtime = PublicIngestRuntime(self.store.database_path, self.sources_path, config.translation_config_path)
        server = create_server(
            "127.0.0.1",
            0,
            self.store,
            self.frontend_dir,
            app_config=config,
            runtime_manager=runtime,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(runtime.stop)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_port}"

    def post_json(self, base_url: str, path: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def get_json(self, base_url: str, path: str) -> dict[str, object]:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_api_created_rsshub_source_enters_public_ingest(self) -> None:
        base_url = self.start_app()
        created = self.post_json(
            base_url,
            "/api/sources",
            {
                "name": "手机新增 RSSHub",
                "source_type": "rsshub",
                "rsshub_base_url": self.upstream_url,
                "rsshub_route": "/rsshub/test",
                "rsshub_healthcheck_path": "/healthz",
                "rsshub_check_health": True,
                "rsshub_instance_name": "fixture-rsshub",
                "tags": ["RSSHub", "手机新增"],
                "language": "zh",
            },
        )["source"]

        self.assertTrue(created["collectable"])
        self.assertEqual(created["collectability_status"], "collectable")

        ingest = self.post_json(base_url, "/api/tasks/ingest-public", {})["ingest"]
        latest = self.get_json(base_url, "/api/items/latest")["items"]

        self.assertEqual(ingest["status"], "success")
        self.assertEqual(ingest["items_created"], 1)
        self.assertTrue(any(item["title"] == "手机新增 RSSHub 来源" for item in latest))

    def test_api_created_json_without_mapping_is_directory_only_for_ingest(self) -> None:
        base_url = self.start_app()
        created = self.post_json(
            base_url,
            "/api/sources",
            {
                "name": "未配置 mapping 的 JSON",
                "source_type": "json",
                "location": f"{self.upstream_url}/items.json",
                "tags": ["JSON"],
            },
        )["source"]

        ingest = self.post_json(base_url, "/api/tasks/ingest-public", {})["ingest"]
        latest = self.get_json(base_url, "/api/items/latest")["items"]

        self.assertFalse(created["collectable"])
        self.assertEqual(created["collectability_status"], "mapping_required")
        self.assertEqual(ingest["status"], "success")
        self.assertEqual(ingest["items_created"], 0)
        self.assertFalse(any(item["title"] == "未配置 mapping 的 JSON" for item in latest))


if __name__ == "__main__":
    unittest.main()
