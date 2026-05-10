from __future__ import annotations

import functools
import json
import sys
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import (
    HtmlSourceAdapter,
    JsonSourceAdapter,
    PublicSourceConfig,
    RssHubSourceAdapter,
    RssSourceAdapter,
    build_public_adapters,
    load_public_source_configs,
)
from signal_harbor.core import IngestPipeline
from signal_harbor.storage import SQLiteStore


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class PublicSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = SQLiteStore(self.root / "public.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def start_server(self) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
        handler = functools.partial(QuietHandler, directory=str(self.root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    def test_rss_adapter_parses_local_http_feed(self) -> None:
        (self.root / "feed.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <item>
                  <title>RSS 监管风险</title>
                  <link>https://example.test/rss-risk</link>
                  <description><![CDATA[监管风险和 AI 芯片供应更新]]></description>
                  <pubDate>Thu, 30 Apr 2026 09:30:00 +0800</pubDate>
                </item>
              </channel>
            </rss>
            """,
            encoding="utf-8",
        )
        server, thread, base_url = self.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        adapter = RssSourceAdapter(
            PublicSourceConfig(name="RSS 测试源", source_type="rss", url=f"{base_url}/feed.xml", tags=["RSS"])
        )
        run = IngestPipeline(self.store).run_adapter(adapter)

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 1)
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["source_type"], "rss")
        self.assertEqual(item["source_url"], "https://example.test/rss-risk")
        self.assertIn("监管", item["tags"])
        self.assertEqual(self.store.get_item(item["id"])["insight"]["evidence_refs"][0]["url"], item["source_url"])

    def test_json_adapter_uses_field_mapping_and_deduplicates(self) -> None:
        payload = {
            "payload": {
                "records": [
                    {
                        "headline": "JSON 芯片订单",
                        "body": "芯片订单热度上升，供应风险仍需观察。",
                        "link": "https://example.test/json-chip",
                        "time": "2026-04-30T11:00:00+08:00",
                        "writer": "json-author",
                        "labels": ["芯片", "订单"],
                    },
                    {
                        "headline": "JSON 芯片订单",
                        "body": "芯片订单热度上升，供应风险仍需观察。",
                        "link": "https://example.test/json-chip",
                        "time": "2026-04-30T11:00:00+08:00",
                        "writer": "json-author",
                        "labels": ["芯片", "订单"],
                    },
                ]
            }
        }
        json_path = self.root / "items.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        config = PublicSourceConfig(
            name="JSON 测试源",
            source_type="json",
            url=str(json_path),
            tags=["JSON"],
            json_mapping={
                "items_path": "payload.records",
                "title": "headline",
                "text": "body",
                "url": "link",
                "published_at": "time",
                "author": "writer",
                "tags": "labels",
            },
        )

        run = IngestPipeline(self.store).run_adapter(JsonSourceAdapter(config))

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 2)
        self.assertEqual(run.items_created, 1)
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["source_type"], "json")
        self.assertEqual(item["author"], "json-author")
        self.assertIn("JSON", item["tags"])

    def test_source_metadata_and_keyword_filters_are_applied(self) -> None:
        payload = {
            "items": [
                {
                    "title": "Nasdaq AI market signal",
                    "text": "AI software and semiconductor demand improved.",
                    "url": "https://example.test/nasdaq-ai",
                    "published_at": "2026-04-30T11:00:00+08:00",
                },
                {
                    "title": "Upcoming Dividend Run For TEST",
                    "text": "Dividend reminder for a low value short item.",
                    "url": "https://example.test/nasdaq-dividend",
                    "published_at": "2026-04-30T12:00:00+08:00",
                },
                {
                    "title": "Weather note",
                    "text": "General weather note without tracked terms.",
                    "url": "https://example.test/nasdaq-weather",
                    "published_at": "2026-04-30T13:00:00+08:00",
                },
            ]
        }
        json_path = self.root / "filtered.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        config = PublicSourceConfig(
            name="Nasdaq 测试源",
            source_type="json",
            url=str(json_path),
            description="高噪声市场新闻源",
            publisher="Nasdaq",
            region="US",
            market="equity",
            language="en",
            quality_tier="media",
            tags=["Nasdaq"],
            default_topics=["美股"],
            include_keywords=["AI", "market"],
            exclude_keywords=["Dividend"],
            json_mapping={"items_path": "items"},
        )

        run = IngestPipeline(self.store).run_adapter(JsonSourceAdapter(config))

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 3)
        self.assertEqual(run.items_created, 1)
        self.assertEqual(run.items_filtered, 2)
        self.assertIn("Dividend", json.dumps(run.metadata, ensure_ascii=False))
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["title"], "Nasdaq AI market signal")
        self.assertIn("美股", item["tags"])
        source = self.store.get_source(config.to_source().id)
        self.assertEqual(source["description"], "高噪声市场新闻源")
        self.assertEqual(source["publisher"], "Nasdaq")
        self.assertEqual(source["market"], "equity")
        self.assertEqual(source["exclude_keywords"], ["Dividend"])
        self.assertEqual(self.store.list_task_runs()[0]["items_filtered"], 2)

    def test_config_loader_and_failed_source_isolation(self) -> None:
        good_path = self.root / "good.json"
        good_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "正常 JSON 公开源",
                            "text": "AI 订单更新",
                            "url": "https://example.test/good",
                            "published_at": "2026-04-30T12:00:00+08:00",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path = self.root / "sources.json"
        config_path.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "name": "关闭源",
                            "type": "json",
                            "url": str(self.root / "disabled.json"),
                            "enabled": False,
                        },
                        {
                            "name": "正常源",
                            "type": "json",
                            "url": str(good_path),
                            "enabled": True,
                            "description": "正常 JSON 源",
                            "publisher": "Fixture Publisher",
                            "region": "local",
                            "market": "fixture",
                            "language": "zh",
                            "quality_tier": "fixture",
                            "default_topics": ["公开源"],
                            "include_keywords": ["AI"],
                            "exclude_keywords": ["ignore-me"],
                            "json_mapping": {"items_path": "items"},
                        },
                        {
                            "name": "失败源",
                            "type": "rss",
                            "url": str(self.root / "missing.xml"),
                            "enabled": True,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        configs = load_public_source_configs(str(config_path))
        adapters = build_public_adapters(configs)
        runs = IngestPipeline(self.store).run_adapters(adapters)

        self.assertEqual(len(configs), 3)
        self.assertEqual(configs[1].description, "正常 JSON 源")
        self.assertEqual(configs[1].include_keywords, ["AI"])
        self.assertEqual(configs[1].default_topics, ["公开源"])
        self.assertEqual(len(adapters), 2)
        self.assertEqual([run.status for run in runs], ["success", "failed"])
        self.assertEqual(self.store.health()["items"], 1)
        self.assertEqual({task["status"] for task in self.store.list_task_runs()}, {"success", "failed"})

    def test_rsshub_adapter_parses_local_http_route_filters_and_ingests(self) -> None:
        (self.root / "healthz").write_text("ok", encoding="utf-8")
        (self.root / "finance.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0">
              <channel>
                <item>
                  <title>RSSHub A股监管更新</title>
                  <link>https://example.test/rsshub/a-stock</link>
                  <description><![CDATA[A股监管政策更新，包含 AI 芯片供应风险。]]></description>
                  <pubDate>Sat, 09 May 2026 09:00:00 +0800</pubDate>
                  <author>rsshub-author</author>
                  <category>A股</category>
                </item>
                <item>
                  <title>RSSHub 彩票噪声</title>
                  <link>https://example.test/rsshub/lottery</link>
                  <description>彩票营销内容。</description>
                  <pubDate>Sat, 09 May 2026 10:00:00 +0800</pubDate>
                </item>
                <item>
                  <title>RSSHub 天气提示</title>
                  <link>https://example.test/rsshub/weather</link>
                  <description>天气内容。</description>
                  <pubDate>Sat, 09 May 2026 11:00:00 +0800</pubDate>
                </item>
              </channel>
            </rss>
            """,
            encoding="utf-8",
        )
        server, thread, base_url = self.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)

        config = PublicSourceConfig.from_dict(
            {
                "name": "RSSHub 本地财经源",
                "type": "rsshub",
                "rsshub_base_url": base_url,
                "rsshub_route": "/finance.xml",
                "rsshub_instance_name": "local-test-rsshub",
                "tags": ["RSSHub"],
                "publisher": "RSSHub",
                "region": "CN",
                "market": "equity",
                "language": "zh",
                "quality_tier": "rsshub",
                "default_topics": ["财经"],
                "include_keywords": ["A股", "监管"],
                "exclude_keywords": ["彩票"],
            }
        )
        adapters = build_public_adapters([config])
        self.assertIsInstance(adapters[0], RssHubSourceAdapter)
        self.assertEqual(config.url, f"{base_url}/finance.xml")

        run = IngestPipeline(self.store).run_adapter(adapters[0])

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 3)
        self.assertEqual(run.items_created, 1)
        self.assertEqual(run.items_filtered, 2)
        item = self.store.list_latest_items(source_id=config.to_source().id)[0]
        self.assertEqual(item["source_type"], "rsshub")
        self.assertEqual(item["author"], "rsshub-author")
        self.assertIn("RSSHub", item["tags"])
        self.assertIn("A股", item["tags"])
        self.assertEqual(item["source_url"], "https://example.test/rsshub/a-stock")
        source = self.store.get_source(config.to_source().id)
        self.assertEqual(source["source_type"], "rsshub")
        self.assertEqual(source["rsshub_base_url"], base_url)
        self.assertEqual(source["rsshub_route"], "/finance.xml")
        self.assertEqual(source["rsshub_url"], f"{base_url}/finance.xml")
        self.assertIn("rsshub", run.metadata)

    def test_rsshub_404_failure_isolated_from_other_sources(self) -> None:
        (self.root / "healthz").write_text("ok", encoding="utf-8")
        (self.root / "good.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0"><channel><item><title>RSSHub 正常监管</title>
            <link>https://example.test/rsshub/good</link>
            <description>A股监管更新。</description></item></channel></rss>
            """,
            encoding="utf-8",
        )
        server, thread, base_url = self.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        good = PublicSourceConfig.from_dict(
            {"name": "RSSHub 正常源", "type": "rsshub", "rsshub_base_url": base_url, "rsshub_route": "/good.xml"}
        )
        missing = PublicSourceConfig.from_dict(
            {"name": "RSSHub 404 源", "type": "rsshub", "rsshub_base_url": base_url, "rsshub_route": "/missing.xml"}
        )

        runs = IngestPipeline(self.store).run_adapters(build_public_adapters([good, missing]))

        self.assertEqual([run.status for run in runs], ["success", "failed"])
        self.assertEqual(self.store.health()["items"], 1)
        self.assertIn("404", runs[1].error or "")
        failed_source = self.store.get_source(missing.to_source().id)
        self.assertIn("404", failed_source["last_error"])

    def test_rsshub_empty_feed_and_non_rss_response_are_failed(self) -> None:
        (self.root / "healthz").write_text("ok", encoding="utf-8")
        (self.root / "empty.xml").write_text(
            """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel></channel></rss>""",
            encoding="utf-8",
        )
        (self.root / "not-rss.xml").write_text("""<html><body>not rss</body></html>""", encoding="utf-8")
        server, thread, base_url = self.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        empty = PublicSourceConfig.from_dict(
            {"name": "RSSHub 空 feed", "type": "rsshub", "rsshub_base_url": base_url, "rsshub_route": "/empty.xml"}
        )
        non_rss = PublicSourceConfig.from_dict(
            {"name": "RSSHub 非 RSS", "type": "rsshub", "rsshub_base_url": base_url, "rsshub_route": "/not-rss.xml"}
        )

        runs = IngestPipeline(self.store).run_adapters(build_public_adapters([empty, non_rss]))

        self.assertEqual([run.status for run in runs], ["failed", "failed"])
        self.assertIn("empty feed", runs[0].error or "")
        self.assertIn("non-RSS", runs[1].error or "")
        self.assertEqual(self.store.health()["items"], 0)

    def test_rsshub_real_route_fixtures_include_chinese_route_encoding(self) -> None:
        (self.root / "healthz").write_text("ok", encoding="utf-8")

        def write_route(route: str, title: str, description: str, category: str) -> None:
            target = self.root.joinpath(*route.strip("/").split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"""<?xml version="1.0" encoding="UTF-8"?>
                <rss version="2.0">
                  <channel>
                    <item>
                      <title>{title}</title>
                      <link>https://example.test{route}</link>
                      <description>{description}</description>
                      <pubDate>Sat, 09 May 2026 10:00:00 +0800</pubDate>
                      <author>RSSHub Fixture</author>
                      <category>{category}</category>
                    </item>
                  </channel>
                </rss>
                """,
                encoding="utf-8",
            )

        routes = [
            ("/wallstreetcn/live/global/2", "华尔街见闻重要快讯", "全球市场重要快讯。", "重要快讯"),
            ("/cls/telegraph/red", "财联社加红电报", "财联社加红电报事件。", "加红电报"),
            ("/jin10/important", "金十重要快讯", "金十重要宏观快讯。", "重要快讯"),
            ("/10jqka/realtimenews/重要", "同花顺重要要闻", "同花顺重要要闻事件。", "重要要闻"),
            ("/dw/rss/rss-chi-all", "DW 中文国际新闻", "DW 中文国际新闻事件。", "国际新闻"),
        ]
        for route, title, description, category in routes:
            write_route(route, title, description, category)

        server, thread, base_url = self.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        configs = [
            PublicSourceConfig.from_dict(
                {
                    "name": title,
                    "type": "rsshub",
                    "rsshub_base_url": base_url,
                    "rsshub_route": route,
                    "rsshub_instance_name": "fixture-rsshub",
                    "tags": ["RSSHub"],
                    "default_topics": [category],
                    "publisher": "RSSHub Fixture",
                    "region": "fixture",
                    "market": "fixture",
                    "language": "zh",
                    "quality_tier": "rsshub",
                }
            )
            for route, title, _description, category in routes
        ]

        encoded_config = [config for config in configs if config.rsshub_route.endswith("/重要")][0]
        self.assertIn("%E9%87%8D%E8%A6%81", encoded_config.url)
        runs = IngestPipeline(self.store).run_adapters(build_public_adapters(configs))

        self.assertEqual([run.status for run in runs], ["success", "success", "success", "success", "success"])
        self.assertEqual(self.store.health()["items"], 5)
        titles = {item["title"] for item in self.store.list_latest_items(limit=10)}
        for _route, title, _description, category in routes:
            self.assertIn(title, titles)
            source = self.store.get_source(PublicSourceConfig.from_dict({
                "name": title,
                "type": "rsshub",
                "rsshub_base_url": base_url,
                "rsshub_route": _route,
            }).to_source().id)
            self.assertEqual(source["rsshub_route"], _route)
            self.assertIn(category, json.dumps(self.store.search_items(query=title), ensure_ascii=False))

    def test_html_adapter_parses_local_file_and_ingests_searchable_items(self) -> None:
        detail_dir = self.root / "details"
        detail_dir.mkdir()
        (detail_dir / "ai.html").write_text(
            """<html><body><main class="detail-body">AI 监管风险详情正文，提到芯片供应和市场验证。</main></body></html>""",
            encoding="utf-8",
        )
        (detail_dir / "fed.html").write_text(
            """<html><body><main class="detail-body">美联储政策详情正文，强调流动性风险。</main></body></html>""",
            encoding="utf-8",
        )
        (detail_dir / "hk.html").write_text(
            """<html><body><main class="detail-body">港股监管讨论详情正文，关注上市规则变化。</main></body></html>""",
            encoding="utf-8",
        )
        index_path = self.root / "forum.html"
        index_path.write_text(
            """<!doctype html>
            <html><body>
              <article class="post">
                <h2 class="title"><a href="details/ai.html">论坛 AI 监管风险</a></h2>
                <p class="summary">列表摘要会被详情页正文替换。</p>
                <time class="published">2026-05-01T09:00:00+08:00</time>
                <span class="author">analyst-a</span>
                <span class="tag">AI</span><span class="tag">监管</span>
              </article>
              <article class="post">
                <h2 class="title"><a href="details/fed.html">论坛 美联储流动性</a></h2>
                <p class="summary">列表摘要二。</p>
                <time class="published">2026-05-01T10:00:00+08:00</time>
                <span class="author">analyst-b</span>
                <span class="tag">美联储</span>
              </article>
              <article class="post">
                <h2 class="title"><a href="details/hk.html">论坛 港股规则更新</a></h2>
                <p class="summary">列表摘要三。</p>
                <time class="published">2026-05-01T11:00:00+08:00</time>
                <span class="author">analyst-c</span>
                <span class="tag">港股</span>
              </article>
            </body></html>
            """,
            encoding="utf-8",
        )
        config = PublicSourceConfig(
            name="本地 HTML 论坛源",
            source_type="html",
            url=str(index_path),
            tags=["HTML"],
            html_mapping={
                "items": "article.post",
                "title": ".title",
                "text": ".summary",
                "url": {"selector": ".title a", "attribute": "href"},
                "published_at": ".published",
                "author": ".author",
                "tags": ".tag",
                "fetch_detail": True,
                "detail_text": ".detail-body",
            },
        )

        adapter = HtmlSourceAdapter(config)
        raw_items = adapter.collect()
        self.assertEqual(len(raw_items), 3)
        self.assertEqual(raw_items[0].author, "analyst-a")
        self.assertIn("AI", raw_items[0].tags)
        self.assertTrue(raw_items[0].source_url.endswith("details/ai.html"))
        self.assertIn("详情正文", raw_items[0].text)

        run = IngestPipeline(self.store).run_adapter(HtmlSourceAdapter(config))

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 3)
        self.assertEqual(run.items_created, 3)
        searched = self.store.search_items(query="AI", source_id=config.to_source().id)
        self.assertEqual(searched[0]["source_type"], "html")
        self.assertEqual(searched[0]["author"], "analyst-a")
        self.assertEqual(self.store.list_latest_items(source_id=config.to_source().id)[0]["source_type"], "html")
        self.assertEqual(self.store.get_item(searched[0]["id"])["insight"]["evidence_refs"][0]["url"], searched[0]["source_url"])

    def test_html_adapter_parses_local_http_page_filters_and_isolates_detail_failure(self) -> None:
        (self.root / "detail-ok.html").write_text(
            """<html><body><section id="body">HTTP AI 详情正文包含监管风险。</section></body></html>""",
            encoding="utf-8",
        )
        (self.root / "forum-http.html").write_text(
            """<!doctype html>
            <html><body>
              <div class="thread" data-kind="post">
                <a class="thread-title" href="/detail-ok.html">HTTP AI 监管观察</a>
                <p class="excerpt">AI software demand improved.</p>
                <span class="time">2026-05-02T09:00:00+08:00</span>
                <span class="writer">writer-a</span>
                <span class="label">AI</span>
              </div>
              <div class="thread" data-kind="post">
                <a class="thread-title" href="/missing-detail.html">HTTP AI 供应链风险</a>
                <p class="excerpt">AI supply chain risk remains.</p>
                <span class="time">2026-05-02T10:00:00+08:00</span>
                <span class="writer">writer-b</span>
                <span class="label">AI</span>
              </div>
              <div class="thread" data-kind="post">
                <a class="thread-title" href="/dividend.html">Upcoming Dividend Run</a>
                <p class="excerpt">Dividend reminder only.</p>
                <span class="time">2026-05-02T11:00:00+08:00</span>
              </div>
              <div class="thread" data-kind="post">
                <a class="thread-title" href="/weather.html">Weather note</a>
                <p class="excerpt">No tracked market term.</p>
                <span class="time">2026-05-02T12:00:00+08:00</span>
              </div>
            </body></html>
            """,
            encoding="utf-8",
        )
        server, thread, base_url = self.start_server()
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        config = PublicSourceConfig(
            name="HTTP HTML 论坛源",
            source_type="html",
            url=f"{base_url}/forum-http.html",
            tags=["HTML", "论坛"],
            default_topics=["公开页"],
            include_keywords=["AI"],
            exclude_keywords=["Dividend"],
            html_mapping={
                "items": ".thread[data-kind='post']",
                "title": ".thread-title",
                "text": ".excerpt",
                "url": ".thread-title",
                "published_at": ".time",
                "author": ".writer",
                "tags": ".label",
                "fetch_detail": True,
                "detail_text": "#body",
            },
        )

        adapters = build_public_adapters([config])
        self.assertIsInstance(adapters[0], HtmlSourceAdapter)
        run = IngestPipeline(self.store).run_adapter(adapters[0])

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 4)
        self.assertEqual(run.items_created, 2)
        self.assertEqual(run.items_filtered, 2)
        self.assertIn("detail_errors", run.metadata)
        self.assertIn("missing-detail", json.dumps(run.metadata, ensure_ascii=False))
        items = self.store.search_items(query="AI", source_id=config.to_source().id)
        self.assertEqual(len(items), 2)
        self.assertTrue(items[0]["source_url"].startswith(base_url))
        self.assertIn("公开页", items[0]["tags"])


if __name__ == "__main__":
    unittest.main()
