from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import FixtureSourceAdapter, JsonSourceAdapter, PublicSourceConfig
from signal_harbor.adapters.base import RawContent
from signal_harbor.api import create_server
from signal_harbor.config import AppConfig
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import Notification, Source
from signal_harbor.runtime import PublicIngestRuntime
from signal_harbor.storage import SQLiteStore


class MemorySourceAdapter:
    def __init__(self, source: Source, items: list[RawContent]) -> None:
        self.source = source
        self.items = items
        self.filtered_count = 0

    def collect(self) -> list[RawContent]:
        return self.items


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.frontend_dir = self.root / "frontend"
        self.frontend_dir.mkdir()
        (self.frontend_dir / "index.html").write_text("<h1>Signal Harbor</h1>", encoding="utf-8")
        self.fixture_path = self.root / "items.json"
        self.fixture_path.write_text(
            json.dumps(
                [
                    {
                        "source_url": "https://example.test/api",
                        "title": "API 监管风险",
                        "text": "监管风险和 AI 芯片供应更新",
                        "published_at": "2026-04-30T13:00:00+08:00",
                        "tags": ["监管", "AI"],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.store = SQLiteStore(self.root / "api.sqlite3")
        IngestPipeline(self.store).run_adapter(FixtureSourceAdapter(self.fixture_path))
        self.server = create_server("127.0.0.1", 0, self.store, self.frontend_dir)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()
        self.store.close()
        self.tempdir.cleanup()

    def get_json(self, path: str) -> dict[str, object]:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def start_runtime_server(self, runtime: PublicIngestRuntime, app_config: AppConfig):
        server = create_server(
            "127.0.0.1",
            0,
            self.store,
            self.frontend_dir,
            app_config=app_config,
            runtime_manager=runtime,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.shutdown)
        self.addCleanup(runtime.stop)
        return f"http://127.0.0.1:{server.server_port}"

    def ingest_event_pair(self) -> tuple[dict[str, object], set[str]]:
        pipeline = IngestPipeline(self.store)
        pipeline.run_adapter(
            MemorySourceAdapter(
                Source(id="src_event_a", name="事件来源 A", source_type="rss", location="memory://event-a"),
                [
                    RawContent(
                        source_id="src_event_a",
                        source_type="rss",
                        source_url="https://event-a.test/catl-storage-order",
                        title="宁德时代获海外储能订单",
                        text="宁德时代确认海外储能订单增长，存在监管风险。",
                        published_at="2026-05-09T10:00:00+08:00",
                        tags=["宁德时代", "储能", "订单"],
                    )
                ],
            )
        )
        pipeline.run_adapter(
            MemorySourceAdapter(
                Source(id="src_event_b", name="事件来源 B", source_type="rss", location="memory://event-b"),
                [
                    RawContent(
                        source_id="src_event_b",
                        source_type="rss",
                        source_url="https://event-b.test/catl-storage-order",
                        title="海外储能订单推动宁德时代供应增长",
                        text="宁德时代海外储能订单带来供应更新和风险提示。",
                        published_at="2026-05-09T10:30:00+08:00",
                        tags=["宁德时代", "储能", "订单"],
                    )
                ],
            )
        )
        latest = self.get_json("/api/items/latest")["items"]
        group = next(item for item in latest if item["related_count"] == 1 and "宁德时代" in json.dumps(item, ensure_ascii=False))
        self.store.add_notification(Notification(item_id=group["id"], title=f"提醒：{group['title']}", message="高分同事件提醒"))
        item_ids = {group["id"], *[related["id"] for related in group["related_items"]]}
        return group, item_ids

    def test_minimal_api_endpoints(self) -> None:
        health = self.get_json("/api/health")
        self.assertTrue(health["ok"])

        latest = self.get_json("/api/items/latest")
        item_id = latest["items"][0]["id"]
        source_id = latest["items"][0]["source_id"]
        self.assertEqual(latest["items"][0]["title"], "API 监管风险")
        self.assertEqual(latest["items"][0]["source_name"], "本地 fixture 文本源")
        self.assertIn("source_publisher", latest["items"][0])
        latest_by_source = self.get_json(f"/api/items/latest?source_id={urllib.parse.quote(source_id)}")
        self.assertEqual(len(latest_by_source["items"]), 1)
        self.assertEqual(latest_by_source["items"][0]["id"], item_id)
        self.assertEqual(self.get_json("/api/items/latest?source_id=missing")["items"], [])

        detail = self.get_json(f"/api/items/{item_id}")
        self.assertEqual(detail["item"]["source_name"], "本地 fixture 文本源")
        self.assertEqual(detail["item"]["insight"]["evidence_refs"][0]["url"], "https://example.test/api")

        query = urllib.parse.urlencode(
            {
                "query": "监管",
                "topic": "AI",
                "min_score": "20",
                "published_from": "2026-04-30T00:00:00+08:00",
                "published_to": "2026-04-30T23:59:59+08:00",
            }
        )
        search = self.get_json(f"/api/items/search?{query}")
        self.assertEqual(len(search["items"]), 1)

        self.post_json("/api/favorites", {"item_id": item_id, "note": "API 收藏"})
        self.assertEqual(len(self.get_json("/api/favorites")["favorites"]), 1)

        self.post_json("/api/watchlists", {"name": "政策", "keywords": ["监管"]})
        self.assertEqual(len(self.get_json("/api/watchlists")["watchlists"]), 1)

        self.post_json(
            "/api/sources",
            {
                "name": "API 数据源说明",
                "source_type": "rss",
                "location": "https://example.test/feed.xml",
                "tags": ["API"],
                "description": "API 创建的数据源介绍",
                "publisher": "API Publisher",
                "region": "US",
                "market": "macro",
                "language": "en",
                "quality_tier": "official",
                "include_keywords": ["AI"],
                "exclude_keywords": ["Dividend"],
                "default_topics": ["宏观"],
            },
        )
        api_source = [source for source in self.get_json("/api/sources")["sources"] if source["name"] == "API 数据源说明"][0]
        self.assertEqual(api_source["description"], "API 创建的数据源介绍")
        self.assertEqual(api_source["publisher"], "API Publisher")
        self.assertEqual(api_source["filter_summary"]["exclude_keywords"], ["Dividend"])
        toggled_source = self.post_json(f"/api/sources/{api_source['id']}/toggle", {"enabled": False})
        self.assertFalse(toggled_source["source"]["enabled"])

        self.post_json(
            "/api/sources",
            {
                "name": "API RSSHub 数据源说明",
                "source_type": "rsshub",
                "location": "http://127.0.0.1:1200/eastmoney",
                "tags": ["RSSHub"],
                "description": "API 创建的 RSSHub 数据源介绍",
                "publisher": "RSSHub",
                "region": "CN",
                "market": "equity",
                "language": "zh",
                "quality_tier": "rsshub",
                "rsshub_base_url": "http://127.0.0.1:1200",
                "rsshub_route": "/eastmoney",
                "rsshub_healthcheck_path": "/healthz",
                "rsshub_check_health": True,
                "rsshub_instance_name": "local-rsshub",
            },
        )
        rsshub_source = [source for source in self.get_json("/api/sources")["sources"] if source["name"] == "API RSSHub 数据源说明"][0]
        self.assertEqual(rsshub_source["source_type"], "rsshub")
        self.assertEqual(rsshub_source["rsshub_base_url"], "http://127.0.0.1:1200")
        self.assertEqual(rsshub_source["rsshub_route"], "/eastmoney")
        self.assertEqual(rsshub_source["rsshub_instance_name"], "local-rsshub")

        self.post_json("/api/collections", {"name": "政策专题", "item_ids": [item_id]})
        self.assertEqual(len(self.get_json("/api/collections")["collections"]), 1)

        empty_collection = self.post_json("/api/collections", {"name": "空专题", "item_ids": []})["collection"]
        appended = self.post_json(f"/api/collections/{empty_collection['id']}/items", {"item_id": item_id})
        self.assertEqual(appended["collection"]["item_ids"], [item_id])
        appended_again = self.post_json(f"/api/collections/{empty_collection['id']}/items", {"item_id": item_id})
        self.assertEqual(appended_again["collection"]["item_ids"], [item_id])

        self.post_json("/api/saved-searches", {"name": "监管搜索", "query": {"query": "监管", "min_score": "20"}})
        saved_searches = self.get_json("/api/saved-searches")["saved_searches"]
        self.assertEqual(saved_searches[0]["query"]["query"], "监管")

        alert_rule = self.post_json("/api/alert-rules", {"name": "高分", "keywords": ["风险"], "min_score": 50})["alert_rule"]
        self.assertEqual(len(self.get_json("/api/alert-rules")["alert_rules"]), 1)
        toggled = self.post_json(f"/api/alert-rules/{alert_rule['id']}/toggle", {"enabled": False})
        self.assertFalse(toggled["alert_rule"]["enabled"])

        self.assertGreaterEqual(len(self.get_json("/api/task-runs")["task_runs"]), 1)
        self.store.add_notification(Notification(title="系统任务", message="未关联具体情报"))
        notifications = self.get_json("/api/notifications")["notifications"]
        self.assertGreaterEqual(len(notifications), 2)
        item_notification = [item for item in notifications if item["item_id"] == item_id][0]
        self.assertEqual(item_notification["item_title"], "API 监管风险")
        self.assertEqual(item_notification["source_id"], source_id)
        self.assertTrue(item_notification["source_name"])
        self.assertEqual(item_notification["source_url"], "https://example.test/api")
        self.assertIn("监管", item_notification["tags"])
        self.assertIn("风险", item_notification["risk_flags"])
        self.assertTrue(item_notification["summary"])
        self.assertIn("translated_summary", item_notification)
        system_notification = [item for item in notifications if item["title"] == "系统任务"][0]
        self.assertFalse(system_notification["is_clickable"])
        self.assertEqual(system_notification["translation_status"], "system")
        self.assertEqual(system_notification["detail_url"], "")
        self.assertEqual(system_notification["system_note"], "这条消息未关联具体情报，无法打开情报详情。")

    def test_latest_search_detail_and_notifications_return_event_group_fields(self) -> None:
        group, item_ids = self.ingest_event_pair()

        self.assertEqual(group["source_count"], 2)
        self.assertEqual(group["related_count"], 1)
        self.assertTrue(group["event_key"])
        self.assertIn("event_group", group)
        self.assertIn("matched_tokens", group)
        self.assertIn("matched_topics", group)
        self.assertIn("time_window", group)
        self.assertIn("conflict_guard", group)
        self.assertIn("宁德时代", group["matched_topics"])
        self.assertTrue(group["time_window"]["within_window"])
        self.assertEqual(len(group["event_evidence_refs"]), 2)
        self.assertEqual(sorted(group["event_sources"]), ["事件来源 A", "事件来源 B"])

        events = self.get_json("/api/events")["events"]
        event = [item for item in events if item["event_key"] == group["event_key"]][0]
        self.assertEqual(event["item_count"], 2)
        self.assertEqual(event["source_count"], 2)
        self.assertTrue(event["is_compact"])
        self.assertEqual(len(event["event_items"]), 2)
        self.assertIn("标题", event["event_merge_reason"])
        self.assertEqual(event["conflict_guard"]["blocked"], False)

        event_detail = self.get_json(f"/api/events/{urllib.parse.quote(group['event_key'])}")["event"]
        self.assertEqual(event_detail["event_key"], group["event_key"])
        self.assertEqual({item["source_url"] for item in event_detail["event_items"]}, {"https://event-a.test/catl-storage-order", "https://event-b.test/catl-storage-order"})
        self.assertEqual(len(event_detail["event_evidence_refs"]), 2)

        search = self.get_json("/api/items/search?topic=%E5%AE%81%E5%BE%B7%E6%97%B6%E4%BB%A3")["items"]
        event_results = [item for item in search if item["event_key"] == group["event_key"]]
        self.assertEqual(len(event_results), 1)
        self.assertEqual(event_results[0]["related_count"], 1)

        detail = self.get_json(f"/api/items/{group['id']}")["item"]
        self.assertEqual(detail["related_count"], 1)
        self.assertEqual(detail["related_items"][0]["source_url"], "https://event-a.test/catl-storage-order")
        self.assertIn("风险", detail["related_items"][0]["risk_flags"])

        notifications = self.get_json("/api/notifications")["notifications"]
        event_notifications = [item for item in notifications if item.get("item_id") in item_ids]
        self.assertEqual(len(event_notifications), 1)
        self.assertEqual(event_notifications[0]["related_count"], 1)
        self.assertEqual(event_notifications[0]["source_count"], 2)
        self.assertLessEqual(len(event_notifications[0]["related_items"]), 3)

    def test_events_api_keeps_unmerged_items_visible_with_explanation(self) -> None:
        events = self.get_json("/api/events")["events"]
        event = [item for item in events if item["title"] == "API 监管风险"][0]

        self.assertEqual(event["item_count"], 1)
        self.assertEqual(event["related_count"], 0)
        self.assertEqual(event["matched_tokens"], [])
        self.assertEqual(event["matched_topics"], [])
        self.assertEqual(event["time_window"]["hours"], 36)
        self.assertFalse(event["conflict_guard"]["blocked"])
        self.assertEqual(event["event_items"][0]["source_url"], "https://example.test/api")

    def test_industry_domains_api_returns_ranked_domains_and_detail(self) -> None:
        pipeline = IngestPipeline(self.store)
        pipeline.run_adapter(
            MemorySourceAdapter(
                Source(
                    id="src_industry_ai",
                    name="行业域来源 A",
                    source_type="rsshub",
                    location="memory://industry-ai",
                    metadata={"quality_tier": "rsshub"},
                ),
                [
                    RawContent(
                        source_id="src_industry_ai",
                        source_type="rsshub",
                        source_url="https://industry.test/ai-capex",
                        title="AI 数据中心资本开支推动光模块订单增长",
                        text="海外 AI 资本开支提升，服务器和光模块订单增长，电网投资受到关注。",
                        published_at="2026-05-10T10:00:00+08:00",
                        tags=["AI", "数据中心", "光模块", "订单"],
                    )
                ],
            )
        )

        payload = self.get_json("/api/industry-domains?window_days=30&limit=10")
        domains = payload["domains"]
        ai_domain = [item for item in domains if item["domain_id"] == "ai-compute-power"][0]

        self.assertGreater(ai_domain["domain_score"], 0)
        self.assertGreater(ai_domain["attention_score"], 0)
        self.assertGreater(ai_domain["benefit_score"], 0)
        self.assertEqual(ai_domain["market_confirmation"], "未接入")
        self.assertIn("short_term_catalyst_score", ai_domain)
        self.assertIn("continuity_score", ai_domain)
        self.assertIn("noise_penalty", ai_domain)
        self.assertIn("recommendation_reason", ai_domain)
        self.assertIn("related_stock_count", ai_domain)
        self.assertGreater(ai_domain["related_stock_count"], 0)
        self.assertEqual(ai_domain["evidence_refs"][0]["source_url"], "https://industry.test/ai-capex")
        self.assertIn("related_events", ai_domain)
        self.assertNotIn("建议买入", json.dumps(ai_domain, ensure_ascii=False))
        self.assertNotIn("推荐买入", json.dumps(ai_domain, ensure_ascii=False))
        self.assertNotIn("仓位建议", json.dumps(ai_domain, ensure_ascii=False))

        detail = self.get_json(f"/api/industry-domains/{urllib.parse.quote(ai_domain['domain_id'])}?window_days=30")["domain"]
        self.assertEqual(detail["domain_id"], "ai-compute-power")
        self.assertIn("score_explanation", detail)
        self.assertIn("next_observation_points", detail)
        self.assertEqual(detail["evidence_refs"][0]["source_name"], "行业域来源 A")
        self.assertIn("related_stocks_top10", detail)
        self.assertLessEqual(len(detail["related_stocks_top10"]), 10)
        self.assertGreater(len(detail["related_stocks_top10"]), 0)
        first_stock = detail["related_stocks_top10"][0]
        self.assertIn("stock_code", first_stock)
        self.assertIn("association_score", first_stock)
        self.assertIn("match_reasons", first_stock)
        self.assertIn("evidence_refs", first_stock)
        self.assertEqual(first_stock["monitoring_metrics"]["market_data"], "未接入")
        self.assertEqual(first_stock["monitoring_metrics"]["excess_return_validation"], "待验证")
        detail_text = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("买入", detail_text)
        self.assertNotIn("卖出", detail_text)
        self.assertNotIn("仓位", detail_text)
        self.assertNotIn("建议买入", detail_text)
        self.assertNotIn("推荐买入", detail_text)
        self.assertNotIn("仓位建议", detail_text)

        with self.assertRaises(urllib.error.HTTPError) as response:
            urllib.request.urlopen(f"{self.base_url}/api/industry-domains/not-found", timeout=5)
        self.assertEqual(response.exception.code, 404)

    def test_industry_domains_api_handles_empty_data(self) -> None:
        payload = self.get_json("/api/industry-domains?window_days=1&limit=5")

        self.assertIn("domains", payload)
        self.assertIsInstance(payload["domains"], list)

    def test_different_events_are_not_grouped(self) -> None:
        pipeline = IngestPipeline(self.store)
        for source_id, source_name, title, url in [
            ("src_rate_hike", "利率来源 A", "美联储宣布加息", "https://rates.test/hike"),
            ("src_rate_cut", "利率来源 B", "美联储宣布降息", "https://rates.test/cut"),
        ]:
            pipeline.run_adapter(
                MemorySourceAdapter(
                    Source(id=source_id, name=source_name, source_type="rss", location=f"memory://{source_id}"),
                    [
                        RawContent(
                            source_id=source_id,
                            source_type="rss",
                            source_url=url,
                            title=title,
                            text=f"{title}，市场风险更新。",
                            published_at="2026-05-09T11:00:00+08:00",
                            tags=["美联储", "利率"],
                        )
                    ],
                )
            )

        latest = self.get_json("/api/items/latest")["items"]
        rate_items = [item for item in latest if item["title"] in {"美联储宣布加息", "美联储宣布降息"}]
        self.assertEqual(len(rate_items), 2)
        self.assertEqual({item["related_count"] for item in rate_items}, {0})
        self.assertEqual(len({item["event_key"] for item in rate_items}), 2)
        self.assertTrue(all(item["matched_tokens"] == [] for item in rate_items))
        self.assertTrue(all(item["conflict_guard"]["blocked"] is False for item in rate_items))

    def test_sources_api_hides_runtime_internal_source(self) -> None:
        self.store.save_source(
            Source(
                id="src_runtime_public_ingest",
                name="公开源采集运行时",
                source_type="runtime",
                location="config/sources.local.json",
                tags=["runtime"],
                enabled=True,
                fetch_interval_minutes=0,
            )
        )

        source_names = [source["name"] for source in self.get_json("/api/sources")["sources"]]

        self.assertNotIn("公开源采集运行时", source_names)

    def test_runtime_status_api_is_sanitized_and_reports_unavailable_runtime(self) -> None:
        status = self.get_json("/api/runtime/status")

        self.assertTrue(status["health"]["ok"])
        self.assertTrue(status["database_configured"])
        self.assertFalse(status["auth_enabled"])
        self.assertEqual(status["scheduler"]["last_status"], "unavailable")
        self.assertNotIn("api.sqlite3", json.dumps(status, ensure_ascii=False))

        request = urllib.request.Request(
            f"{self.base_url}/api/tasks/ingest-public",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as response:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(response.exception.code, 503)

    def test_english_translation_status_is_explainable_in_latest_detail_and_notifications(self) -> None:
        english_path = self.root / "english-etf.json"
        english_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "SCO, XQQI: Big ETF Inflows",
                            "text": "Big ETF Inflows include regulation risk and units outstanding updates.",
                            "url": "https://example.test/api-etf-inflows",
                            "published_at": "2026-05-08T09:30:00+08:00",
                            "tags": ["ETF"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        adapter = JsonSourceAdapter(
            PublicSourceConfig(
                name="API English ETF Source",
                source_type="json",
                url=str(english_path),
                json_mapping={"items_path": "items"},
            )
        )
        IngestPipeline(self.store).run_adapter(adapter)

        latest = self.get_json("/api/items/latest")["items"][0]
        self.assertEqual(latest["lang"], "en")
        self.assertNotEqual(latest["translation_status"], "not_required")
        self.assertEqual(latest["translation"]["status"], "translated")
        self.assertIn("资金流入", latest["translation"]["translated_title"])

        detail = self.get_json(f"/api/items/{latest['id']}")["item"]
        self.assertEqual(detail["translation"]["source_language"], "en")
        self.assertNotEqual(detail["translation"]["status"], "not_required")
        self.assertIn("流通单位", detail["translation"]["translated_summary"])

        self.store.add_notification(Notification(item_id=latest["id"], title=f"提醒：{latest['title']}", message="翻译提醒"))
        notifications = self.get_json("/api/notifications")["notifications"]
        notification = [item for item in notifications if item["item_id"] == latest["id"]][0]
        self.assertEqual(notification["translation_status"], "translated")
        self.assertIn("资金流入", notification["translated_title"])
        self.assertIn("流通单位", notification["translated_summary"])

    def test_manual_public_ingest_api_success_and_failure_paths(self) -> None:
        public_item_path = self.root / "public-runtime.json"
        public_item_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "API 手动公开源采集",
                            "text": "公开源手动采集进入搜索链路，包含 AI 和监管关键词。",
                            "url": "https://example.test/api-runtime-public",
                            "published_at": "2026-05-08T10:00:00+08:00",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        sources_path = self.root / "runtime-sources.json"
        sources_path.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "name": "API 运行公开源",
                            "type": "json",
                            "url": str(public_item_path),
                            "json_mapping": {"items_path": "items"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config = AppConfig(
            data_dir=self.root / "data",
            database_path=self.store.database_path,
            fixture_path=self.fixture_path,
            frontend_dir=self.frontend_dir,
            translation_config_path=ROOT / "config" / "translation.example.json",
            public_sources_config_path=sources_path,
            host="127.0.0.1",
            port=0,
        )
        success_base_url = self.start_runtime_server(
            PublicIngestRuntime(self.store.database_path, sources_path, config.translation_config_path),
            config,
        )
        data = json.dumps({}).encode("utf-8")
        request = urllib.request.Request(
            f"{success_base_url}/api/tasks/ingest-public",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(payload["ingest"]["status"], "success")
        self.assertEqual(payload["ingest"]["items_created"], 1)
        latest = self.get_json("/api/items/latest")["items"]
        self.assertTrue(any(item["title"] == "API 手动公开源采集" for item in latest))

        missing_path = self.root / "missing-sources.json"
        failure_config = AppConfig(
            data_dir=self.root / "data",
            database_path=self.store.database_path,
            fixture_path=self.fixture_path,
            frontend_dir=self.frontend_dir,
            translation_config_path=ROOT / "config" / "translation.example.json",
            public_sources_config_path=missing_path,
            host="127.0.0.1",
            port=0,
        )
        failure_base_url = self.start_runtime_server(
            PublicIngestRuntime(self.store.database_path, missing_path, failure_config.translation_config_path),
            failure_config,
        )
        request = urllib.request.Request(
            f"{failure_base_url}/api/tasks/ingest-public",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            failure_payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(failure_payload["ingest"]["status"], "failed")
        self.assertTrue(failure_payload["ingest"]["error"])
        self.assertTrue(any(task["task_type"] == "ingest-public" for task in self.store.list_task_runs()))


if __name__ == "__main__":
    unittest.main()
