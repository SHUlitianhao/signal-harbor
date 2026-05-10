from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from signal_harbor.api import BasicAuthConfig, create_server
from signal_harbor.config import AppConfig, load_config
from signal_harbor.runtime import PublicIngestRuntime
from signal_harbor.storage import SQLiteStore
from scripts.run_dev import validate_remote_access


class RemoteAccessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.frontend_dir = self.root / "frontend"
        self.frontend_dir.mkdir()
        (self.frontend_dir / "index.html").write_text("<h1>Signal Harbor</h1>", encoding="utf-8")
        self.store = SQLiteStore(self.root / "remote.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def create_test_server(
        self,
        auth_config: BasicAuthConfig | None = None,
        app_config: AppConfig | None = None,
        runtime_manager: PublicIngestRuntime | None = None,
    ):
        server = create_server(
            "127.0.0.1",
            0,
            self.store,
            self.frontend_dir,
            auth_config=auth_config,
            app_config=app_config,
            runtime_manager=runtime_manager,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        if runtime_manager:
            self.addCleanup(runtime_manager.stop)
        return server

    def request(self, base_url: str, path: str, auth: tuple[str, str] | None = None, method: str = "GET"):
        headers = {}
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        data = b"{}" if method == "POST" else None
        if data:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
        return urllib.request.urlopen(request, timeout=5)

    def test_default_local_access_requires_no_auth(self) -> None:
        server = self.create_test_server()
        with self.request(f"http://127.0.0.1:{server.server_port}", "/api/health") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertTrue(payload["ok"])

    def test_config_reads_remote_access_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SIGNAL_HARBOR_CONFIG": "",
                "SIGNAL_HARBOR_REMOTE_ACCESS": "true",
                "SIGNAL_HARBOR_REMOTE_USERNAME": "mobile",
                "SIGNAL_HARBOR_REMOTE_PASSWORD": "secret",
                "SIGNAL_HARBOR_SOURCES_CONFIG": str(self.root / "sources.json"),
                "SIGNAL_HARBOR_INGEST_ON_STARTUP": "true",
                "SIGNAL_HARBOR_INGEST_INTERVAL_MINUTES": "15",
                "SIGNAL_HARBOR_REMOTE_PUBLIC_BASE_URL": "https://signal.example.test",
            },
        ):
            config = load_config()

        self.assertTrue(config.remote_access_enabled)
        self.assertEqual(config.remote_auth_scheme, "basic")
        self.assertEqual(config.remote_auth_username, "mobile")
        self.assertEqual(config.remote_auth_password, "secret")
        self.assertEqual(config.public_sources_config_path, self.root / "sources.json")
        self.assertTrue(config.ingest_on_startup)
        self.assertEqual(config.ingest_interval_minutes, 15)
        self.assertEqual(config.remote_public_base_url, "https://signal.example.test")

    def test_run_dev_rejects_remote_access_without_password(self) -> None:
        config_path = self.root / "app.json"
        config_path.write_text(
            json.dumps(
                {
                    "data_dir": str(self.root / "data"),
                    "database_path": str(self.root / "data" / "signal_harbor.sqlite3"),
                    "fixture_path": str(ROOT / "config" / "fixtures" / "items.json"),
                    "frontend_dir": str(ROOT / "frontend" / "static"),
                    "remote_access_enabled": True,
                    "remote_auth_scheme": "basic",
                    "remote_auth_username": "mobile",
                    "host": "127.0.0.1",
                    "port": 0,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = {**os.environ, "SIGNAL_HARBOR_CONFIG": str(config_path)}
        env.pop("SIGNAL_HARBOR_REMOTE_PASSWORD", None)

        result = subprocess.run(
            [sys.executable, "scripts/run_dev.py"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("缺少 SIGNAL_HARBOR_REMOTE_PASSWORD", result.stderr)

    def test_unprotected_non_local_host_emits_warning(self) -> None:
        config = AppConfig(
            data_dir=self.root / "data",
            database_path=self.root / "data" / "signal_harbor.sqlite3",
            fixture_path=ROOT / "config" / "fixtures" / "items.json",
            frontend_dir=ROOT / "frontend" / "static",
            translation_config_path=ROOT / "config" / "translation.example.json",
            public_sources_config_path=ROOT / "config" / "sources.example.json",
            host="0.0.0.0",
            port=8765,
        )

        valid, message = validate_remote_access(config)

        self.assertTrue(valid)
        self.assertIn("不要直接公网暴露", message)

    def test_remote_basic_auth_guards_api_and_static_files(self) -> None:
        server = self.create_test_server(
            BasicAuthConfig(enabled=True, username="mobile", password="secret", scheme="basic")
        )
        base_url = f"http://127.0.0.1:{server.server_port}"

        with self.assertRaises(urllib.error.HTTPError) as missing_auth:
            self.request(base_url, "/api/health")
        self.assertEqual(missing_auth.exception.code, 401)
        self.assertIn("认证失败", missing_auth.exception.read().decode("utf-8"))

        with self.assertRaises(urllib.error.HTTPError) as wrong_auth:
            self.request(base_url, "/api/health", auth=("mobile", "wrong"))
        self.assertEqual(wrong_auth.exception.code, 401)

        with self.request(base_url, "/api/health", auth=("mobile", "secret")) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ok"])

        with self.request(base_url, "/", auth=("mobile", "secret")) as response:
            html = response.read().decode("utf-8")
        self.assertIn("Signal Harbor", html)

    def test_remote_auth_guards_runtime_status_and_manual_public_ingest(self) -> None:
        item_path = self.root / "runtime-items.json"
        item_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "手机运行采集",
                            "text": "远程手机手动触发公开源采集，包含 AI 风险提示。",
                            "url": "https://example.test/runtime-mobile",
                            "published_at": "2026-05-08T09:00:00+08:00",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sources_path = self.root / "sources.json"
        sources_path.write_text(
            json.dumps({"sources": [{"name": "运行测试源", "type": "json", "url": str(item_path), "json_mapping": {"items_path": "items"}}]}),
            encoding="utf-8",
        )
        config = AppConfig(
            data_dir=self.root / "data",
            database_path=self.store.database_path,
            fixture_path=ROOT / "config" / "fixtures" / "items.json",
            frontend_dir=self.frontend_dir,
            translation_config_path=ROOT / "config" / "translation.example.json",
            public_sources_config_path=sources_path,
            remote_access_enabled=True,
            remote_auth_username="mobile",
            remote_auth_password="secret",
            remote_public_base_url="https://signal.example.test",
            host="127.0.0.1",
            port=0,
        )
        runtime = PublicIngestRuntime(
            database_path=self.store.database_path,
            sources_config_path=sources_path,
            translation_config_path=config.translation_config_path,
        )
        server = self.create_test_server(
            BasicAuthConfig(enabled=True, username="mobile", password="secret", scheme="basic"),
            app_config=config,
            runtime_manager=runtime,
        )
        base_url = f"http://127.0.0.1:{server.server_port}"

        with self.assertRaises(urllib.error.HTTPError) as missing_status_auth:
            self.request(base_url, "/api/runtime/status")
        self.assertEqual(missing_status_auth.exception.code, 401)

        with self.assertRaises(urllib.error.HTTPError) as missing_post_auth:
            self.request(base_url, "/api/tasks/ingest-public", method="POST")
        self.assertEqual(missing_post_auth.exception.code, 401)

        with self.request(base_url, "/api/runtime/status", auth=("mobile", "secret")) as response:
            raw_status = response.read().decode("utf-8")
        self.assertNotIn("secret", raw_status)
        status = json.loads(raw_status)
        self.assertTrue(status["remote_access_enabled"])
        self.assertTrue(status["auth_enabled"])
        self.assertEqual(status["public_base_url"], "https://signal.example.test")

        with self.request(base_url, "/api/tasks/ingest-public", auth=("mobile", "secret"), method="POST") as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(payload["ingest"]["status"], "success")
        self.assertEqual(payload["ingest"]["items_created"], 1)


if __name__ == "__main__":
    unittest.main()
