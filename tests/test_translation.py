from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import JsonSourceAdapter, PublicSourceConfig
from signal_harbor.api import create_server
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import Item, Notification, Source
from signal_harbor.storage import SQLiteStore
from signal_harbor.translation import load_translation_provider


class FailingTranslationProvider:
    name = "failing-dictionary"

    def translate(self, item, summary, tags, risk_flags):  # type: ignore[no-untyped-def]
        raise RuntimeError("dictionary unavailable")


class TranslationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = SQLiteStore(self.root / "translation.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def write_public_json(self, name: str = "fed.json") -> Path:
        return self.write_public_json_items(
            name,
            [
                {
                    "title": "Federal Reserve rates investigation",
                    "text": "Federal Reserve regulation investigation pointed to inflation risks.",
                    "url": "https://example.test/fed-rates",
                    "published_at": "2026-04-30T11:00:00+08:00",
                    "tags": ["earnings"],
                }
            ],
        )

    def write_public_json_items(self, name: str, items: list[dict[str, object]]) -> Path:
        path = self.root / name
        path.write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
        return path

    def fed_adapter(self, path: Path) -> JsonSourceAdapter:
        return self.english_adapter(path, name="Fed Translation Fixture", tags=["Fed", "rates"])

    def english_adapter(self, path: Path, name: str = "English Translation Fixture", tags: list[str] | None = None) -> JsonSourceAdapter:
        return JsonSourceAdapter(
            PublicSourceConfig(
                name=name,
                source_type="json",
                url=str(path),
                language="en",
                tags=tags or [],
                quality_tier="official",
                json_mapping={"items_path": "items"},
            )
        )

    def auto_language_adapter(self, path: Path, name: str = "Auto Language Fixture") -> JsonSourceAdapter:
        return JsonSourceAdapter(
            PublicSourceConfig(
                name=name,
                source_type="json",
                url=str(path),
                tags=[],
                quality_tier="media",
                json_mapping={"items_path": "items"},
            )
        )

    def start_api_server(self) -> tuple[str, object, threading.Thread]:
        frontend_dir = self.root / "frontend"
        frontend_dir.mkdir(exist_ok=True)
        (frontend_dir / "index.html").write_text("<h1>Signal Harbor</h1>", encoding="utf-8")
        server = create_server("127.0.0.1", 0, self.store, frontend_dir)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        return f"http://127.0.0.1:{server.server_port}", server, thread

    def get_json(self, base_url: str, path: str) -> dict[str, object]:
        with urllib.request.urlopen(f"{base_url}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, base_url: str, path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"{base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_translation_example_contains_required_terms(self) -> None:
        config = json.loads((ROOT / "config" / "translation.example.json").read_text(encoding="utf-8"))["translation"]
        self.assertTrue(config["enabled"])
        self.assertEqual(config["provider"], "dictionary")
        self.assertIn("tag_dictionary_path", config)
        dictionary = {**config["dictionary"], **config["tag_dictionary"], **config["risk_dictionary"]}
        for term in [
            "Fed",
            "Federal Reserve",
            "ECB",
            "HKEX",
            "CFTC",
            "HKMA",
            "Nasdaq",
            "earnings",
            "dividend",
            "regulation",
            "sanction",
            "investigation",
            "downgrade",
            "inflation",
            "rates",
            "ETF",
            "Inflows",
            "Big ETF Inflows",
        ]:
            self.assertIn(term, dictionary)

    def test_english_item_without_language_hint_is_not_marked_not_required(self) -> None:
        path = self.write_public_json_items(
            "etf-auto-language.json",
            [
                {
                    "title": "SCO, XQQI: Big ETF Inflows",
                    "text": "ETF units outstanding increased across the coverage universe.",
                    "url": "https://example.test/etf-inflows",
                    "published_at": "2026-05-08T09:30:00+08:00",
                    "tags": ["ETF"],
                }
            ],
        )

        run = IngestPipeline(self.store).run_adapter(self.auto_language_adapter(path))

        self.assertEqual(run.status, "success")
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["lang"], "en")
        self.assertNotEqual(item["translation"]["status"], "not_required")
        self.assertEqual(item["translation"]["source_language"], "en")
        self.assertEqual(item["translation"]["status"], "translated")
        self.assertIn("资金流入", item["translation"]["translated_title"])
        self.assertIn("交易所交易基金", item["translation"]["translated_summary"])

    def test_manual_translate_corrects_english_item_misclassified_as_zh(self) -> None:
        source = Source(
            id="src_legacy_english",
            name="Legacy English Source",
            source_type="json",
            location="https://example.test/legacy.json",
            metadata={"language": "zh"},
        )
        self.store.save_source(source)
        item = Item(
            source_id=source.id,
            source_type="json",
            source_url="https://example.test/legacy-etf",
            title="SCO, XQQI: Big ETF Inflows",
            canonical_text="Big ETF Inflows and units outstanding updates.",
            published_at="2026-05-08T09:30:00+08:00",
            lang="zh",
            tags=["ETF"],
            metadata={
                "language": "zh",
                "translation": {
                    "status": "not_required",
                    "provider": "dictionary",
                    "source_language": "zh",
                    "target_language": "zh",
                },
            },
        )
        self.store.upsert_item(item)
        base_url, _, _ = self.start_api_server()

        latest_before = self.get_json(base_url, "/api/items/latest")["items"][0]
        self.assertEqual(latest_before["translation"]["source_language"], "en")
        self.assertEqual(latest_before["translation_status"], "untranslated")

        translated = self.post_json(base_url, f"/api/items/{item.id}/translate")

        self.assertEqual(translated["translation"]["source_language"], "en")
        self.assertEqual(translated["translation"]["status"], "translated")
        self.assertIn("资金流入", translated["translation"]["translated_title"])
        latest_after = self.get_json(base_url, "/api/items/latest")["items"][0]
        self.assertNotEqual(latest_after["translation_status"], "not_required")
        self.assertIn("资金流入", latest_after["translation"]["translated_title"])

    def test_dictionary_translation_is_saved_and_searchable(self) -> None:
        run = IngestPipeline(self.store).run_adapter(self.fed_adapter(self.write_public_json()))

        self.assertEqual(run.status, "success")
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["lang"], "en")
        translation = item["translation"]
        self.assertEqual(translation["provider"], "dictionary")
        self.assertEqual(translation["source_language"], "en")
        self.assertIn("美联储", translation["translated_title"])
        self.assertIn("通胀", translation["translated_summary"])
        self.assertIn("美联储", translation["translated_tags"])
        self.assertIn("监管", translation["translated_risk_flags"])
        self.assertIn("美联储", item["tags"])

        search_results = self.store.search_items(query="美联储")
        self.assertEqual([result["id"] for result in search_results], [item["id"]])
        detail = self.store.get_item(item["id"])
        translation_extractions = [row for row in detail["extractions"] if row["kind"] == "translation"]
        self.assertEqual(len(translation_extractions), 1)
        self.assertEqual(translation_extractions[0]["metadata"]["provider"], "dictionary")

    def test_translation_failure_does_not_block_ingest(self) -> None:
        run = IngestPipeline(self.store, translation_provider=FailingTranslationProvider()).run_adapter(
            self.fed_adapter(self.write_public_json("failing.json"))
        )

        self.assertEqual(run.status, "success")
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["translation"]["status"], "error")
        self.assertIn("dictionary unavailable", item["translation"]["error"])

    def test_api_returns_translation_object(self) -> None:
        IngestPipeline(self.store).run_adapter(self.fed_adapter(self.write_public_json()))
        base_url, _, _ = self.start_api_server()

        latest = self.get_json(base_url, "/api/items/latest")
        item = latest["items"][0]
        self.assertIn("美联储", item["translation"]["translated_title"])

        query = urllib.parse.urlencode({"query": "美联储"})
        search = self.get_json(base_url, f"/api/items/search?{query}")
        self.assertEqual(search["items"][0]["id"], item["id"])

        detail = self.get_json(base_url, f"/api/items/{item['id']}")
        self.assertEqual(detail["item"]["translation"]["provider"], "dictionary")
        self.assertEqual(detail["item"]["insight"]["evidence_refs"][0]["url"], "https://example.test/fed-rates")

    def test_manual_translate_api_and_notification_fields(self) -> None:
        IngestPipeline(self.store).run_adapter(self.fed_adapter(self.write_public_json()))
        base_url, _, _ = self.start_api_server()
        item = self.get_json(base_url, "/api/items/latest")["items"][0]

        translated = self.post_json(base_url, f"/api/items/{item['id']}/translate")

        self.assertEqual(translated["translation"]["status"], "translated")
        self.assertIn("美联储", translated["translation"]["translated_title"])
        latest_after_translate = self.get_json(base_url, "/api/items/latest")["items"][0]
        self.assertEqual(latest_after_translate["id"], item["id"])
        self.assertIn("美联储", latest_after_translate["translation"]["translated_title"])
        self.assertIn("通胀", latest_after_translate["translation"]["translated_summary"])
        self.assertIn("监管", latest_after_translate["translation"]["translated_risk_flags"])
        detail = self.get_json(base_url, f"/api/items/{item['id']}")
        translation_extractions = [row for row in detail["item"]["extractions"] if row["kind"] == "translation"]
        self.assertGreaterEqual(len(translation_extractions), 1)
        self.assertIn("美联储", detail["item"]["translation"]["translated_title"])
        self.assertIn("通胀", detail["item"]["translation"]["translated_summary"])

        self.store.add_notification(Notification(item_id=item["id"], title=f"提醒：{item['title']}", message="翻译提醒"))
        notifications = self.get_json(base_url, "/api/notifications")["notifications"]
        self.assertGreaterEqual(len(notifications), 1)
        notification = notifications[0]
        self.assertEqual(notification["item_id"], item["id"])
        self.assertEqual(notification["item_title"], item["title"])
        self.assertIn("美联储", notification["translated_title"])
        self.assertIn("通胀", notification["translated_summary"])
        self.assertTrue(notification["source_name"])
        self.assertIn("监管", notification["risk_flags"])
        self.assertIn("美联储", notification["tags"])
        self.assertEqual(notification["translation_status"], "translated")
        self.assertTrue(notification["is_clickable"])
        self.assertEqual(notification["detail_url"], f"/api/items/{item['id']}")
        self.assertEqual(notification["source_url"], "https://example.test/fed-rates")

    def test_user_glossary_terms_override_and_disable_config_dictionary(self) -> None:
        provider = load_translation_provider(
            user_terms=[
                {
                    "source_term": "Federal Reserve",
                    "target_term": "联准会",
                    "category": "dictionary",
                    "enabled": True,
                },
                {
                    "source_term": "rates",
                    "target_term": "利率",
                    "category": "dictionary",
                    "enabled": False,
                },
            ]
        )
        item = Item(
            source_id="src",
            source_type="json",
            source_url="https://example.test/fed",
            title="Federal Reserve rates",
            canonical_text="Federal Reserve rates",
            published_at="2026-04-30T11:00:00+08:00",
            lang="en",
            tags=["rates"],
        )

        translation = provider.translate(item, item.canonical_text, item.tags, [])

        self.assertEqual(translation["status"], "translated")
        self.assertIn("联准会", translation["translated_title"])
        self.assertNotIn("美联储", translation["translated_title"])
        self.assertNotIn("利率", translation["translated_title"])

    def test_glossary_crud_batch_translation_and_status_filters(self) -> None:
        path = self.write_public_json_items(
            "fomc.json",
            [
                {
                    "title": "FOMC dot plot surprise",
                    "text": "FOMC dot plot surprise affects outlook.",
                    "url": "https://example.test/fomc",
                    "published_at": "2026-04-30T12:00:00+08:00",
                    "tags": ["FOMC"],
                }
            ],
        )
        IngestPipeline(self.store).run_adapter(self.english_adapter(path))
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["translation"]["status"], "missing_terms")
        base_url, _, _ = self.start_api_server()

        status = self.get_json(base_url, "/api/translation/status")["translation"]
        self.assertEqual(status["english_items"], 1)
        self.assertEqual(status["missing_terms"], 1)
        self.assertIn("FOMC", [entry["term"] for entry in status["high_frequency_untranslated_terms"]])

        created = self.post_json(
            base_url,
            "/api/translation/glossary",
            {
                "source_term": "FOMC",
                "target_term": "联邦公开市场委员会",
                "category": "dictionary",
                "enabled": True,
                "notes": "test",
            },
        )["term"]
        self.assertEqual(created["source_term"], "FOMC")
        self.assertTrue(created["enabled"])

        batch = self.post_json(base_url, "/api/items/translate-batch", {"status": "missing_terms", "limit": 10})["batch"]
        self.assertEqual(batch["requested"], 1)
        self.assertEqual(batch["failed"], 0)
        self.assertEqual(batch["translated"], 1)

        detail = self.get_json(base_url, f"/api/items/{item['id']}")["item"]
        self.assertEqual(detail["translation"]["status"], "translated")
        self.assertIn("联邦公开市场委员会", detail["translation"]["translated_title"])

        translated_search = self.get_json(base_url, "/api/items/search?translation_status=translated")
        self.assertEqual([row["id"] for row in translated_search["items"]], [item["id"]])
        translated_latest = self.get_json(base_url, "/api/items/latest?translation_status=translated")
        self.assertEqual([row["id"] for row in translated_latest["items"]], [item["id"]])
        missing_search = self.get_json(base_url, "/api/items/search?translation_status=missing_terms")
        self.assertEqual(missing_search["items"], [])

        updated = self.post_json(
            base_url,
            f"/api/translation/glossary/{created['id']}",
            {"target_term": "美联储议息会议"},
        )["term"]
        self.assertEqual(updated["target_term"], "美联储议息会议")
        refreshed = self.post_json(base_url, f"/api/items/{item['id']}/translate")["translation"]
        self.assertIn("美联储议息会议", refreshed["translated_title"])

        disabled = self.post_json(base_url, f"/api/translation/glossary/{created['id']}", {"enabled": False})["term"]
        self.assertFalse(disabled["enabled"])
        missing = self.post_json(base_url, f"/api/items/{item['id']}/translate")["translation"]
        self.assertEqual(missing["status"], "missing_terms")
        self.assertNotIn("美联储议息会议", missing.get("translated_title", ""))

        self.post_json(base_url, f"/api/translation/glossary/{created['id']}/delete")
        glossary = self.get_json(base_url, "/api/translation/glossary")["terms"]
        self.assertEqual([term for term in glossary if term["id"] == created["id"]], [])

    def test_translate_batch_isolates_single_item_failure(self) -> None:
        path = self.write_public_json_items(
            "batch.json",
            [
                {
                    "title": "ACME outlook",
                    "text": "ACME outlook",
                    "url": "https://example.test/acme",
                    "published_at": "2026-04-30T12:00:00+08:00",
                    "tags": ["ACME"],
                },
                {
                    "title": "OMEGA outlook",
                    "text": "OMEGA outlook",
                    "url": "https://example.test/omega",
                    "published_at": "2026-04-30T11:00:00+08:00",
                    "tags": ["OMEGA"],
                },
            ],
        )
        IngestPipeline(self.store).run_adapter(self.english_adapter(path))
        items = self.store.list_latest_items()
        fail_id = items[0]["id"]
        original_apply = self.store.apply_item_translation

        def flaky_apply(item_id: str, translation: dict[str, object]) -> dict[str, object] | None:
            if item_id == fail_id:
                raise RuntimeError("write failed")
            return original_apply(item_id, translation)

        self.store.apply_item_translation = flaky_apply  # type: ignore[method-assign]
        self.addCleanup(lambda: setattr(self.store, "apply_item_translation", original_apply))
        base_url, _, _ = self.start_api_server()

        batch = self.post_json(base_url, "/api/items/translate-batch", {"status": "untranslated", "limit": 2})["batch"]

        self.assertEqual(batch["requested"], 2)
        self.assertEqual(batch["failed"], 1)
        self.assertEqual(batch["translated"], 1)
        self.assertIn("write failed", [item.get("error", "") for item in batch["items"]])


if __name__ == "__main__":
    unittest.main()
