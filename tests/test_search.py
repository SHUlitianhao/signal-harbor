from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import FixtureSourceAdapter, JsonSourceAdapter, PublicSourceConfig, RssSourceAdapter
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import Favorite, Source
from signal_harbor.storage import SQLiteStore


class SearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = SQLiteStore(self.root / "search.sqlite3")

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def ingest_fixture(self, source: Source, items: list[dict[str, object]]) -> None:
        fixture_path = self.root / f"{source.id}.json"
        fixture_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        IngestPipeline(self.store).run_adapter(FixtureSourceAdapter(fixture_path, source=source))

    def test_search_supports_english_entity_and_combined_filters(self) -> None:
        source = Source(id="src_equity", name="股票观察", source_type="fixture", location="memory://equity")
        self.ingest_fixture(
            source,
            [
                {
                    "source_url": "https://example.test/aapl-risk",
                    "title": "AAPL AI 监管风险",
                    "text": "AAPL 供应链监管风险继续发酵，AI 芯片订单需要验证。",
                    "published_at": "2026-04-30T10:00:00+08:00",
                    "tags": ["AI", "监管"],
                },
                {
                    "source_url": "https://example.test/gold",
                    "title": "黄金短线观察",
                    "text": "黄金与美元出现分歧。",
                    "published_at": "2026-04-28T10:00:00+08:00",
                    "tags": ["黄金"],
                },
            ],
        )
        target = self.store.search_items(query="AAPL")[0]
        self.store.add_favorite(Favorite(item_id=target["id"], note="组合过滤样本"))

        results = self.store.search_items(
            query="AAPL",
            source_id=source.id,
            topic="监管",
            min_score=50,
            favorite=True,
            published_from="2026-04-30T00:00:00+08:00",
            published_to="2026-04-30T23:59:59+08:00",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "AAPL AI 监管风险")

    def test_search_supports_chinese_keyword_time_range_and_like_fallback(self) -> None:
        source = Source(id="src_policy", name="政策源", source_type="fixture", location="memory://policy")
        self.ingest_fixture(
            source,
            [
                {
                    "source_url": "https://example.test/regulation-new",
                    "title": "监管细则更新",
                    "text": "监管政策强调风险控制和信息披露。",
                    "published_at": "2026-04-30T09:00:00+08:00",
                    "tags": ["监管", "政策"],
                },
                {
                    "source_url": "https://example.test/regulation-old",
                    "title": "旧监管讨论",
                    "text": "历史监管讨论。",
                    "published_at": "2026-04-20T09:00:00+08:00",
                    "tags": ["监管"],
                },
            ],
        )

        ranged = self.store.search_items(
            query="监管",
            published_from="2026-04-29T00:00:00+08:00",
            published_to="2026-05-01T00:00:00+08:00",
        )
        self.assertEqual([item["title"] for item in ranged], ["监管细则更新"])

        self.store.fts5_enabled = False
        fallback = self.store.search_items(query="监管", topic="政策")
        self.assertEqual([item["title"] for item in fallback], ["监管细则更新"])

    def test_fixture_rss_and_json_items_are_searchable(self) -> None:
        fixture_source = Source(id="src_fixture_search", name="Fixture 搜索源", source_type="fixture", location="memory://fixture")
        self.ingest_fixture(
            fixture_source,
            [
                {
                    "source_url": "https://example.test/fixture-search",
                    "title": "Fixture AI 订单",
                    "text": "Fixture 内容提到 AI 订单和芯片供应。",
                    "published_at": "2026-04-30T08:00:00+08:00",
                    "tags": ["AI"],
                }
            ],
        )

        rss_path = self.root / "feed.xml"
        rss_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
            <rss version="2.0"><channel>
              <item>
                <title>RSS 监管风险</title>
                <link>https://example.test/rss-search</link>
                <description>RSS 内容提到监管风险和供应变化。</description>
                <pubDate>Thu, 30 Apr 2026 10:00:00 +0800</pubDate>
              </item>
            </channel></rss>
            """,
            encoding="utf-8",
        )
        json_path = self.root / "items.json"
        json_path.write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "title": "JSON 美联储观察",
                            "text": "JSON 内容提到美联储和美元变化。",
                            "url": "https://example.test/json-search",
                            "published_at": "2026-04-30T11:00:00+08:00",
                            "tags": ["美联储"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        pipeline = IngestPipeline(self.store)
        pipeline.run_adapters(
            [
                RssSourceAdapter(PublicSourceConfig(name="RSS 搜索源", source_type="rss", url=str(rss_path), tags=["RSS"])),
                JsonSourceAdapter(
                    PublicSourceConfig(
                        name="JSON 搜索源",
                        source_type="json",
                        url=str(json_path),
                        tags=["JSON"],
                        json_mapping={"items_path": "items"},
                    )
                ),
            ]
        )

        self.assertEqual(self.store.search_items(query="Fixture")[0]["source_type"], "fixture")
        self.assertEqual(self.store.search_items(query="RSS")[0]["source_type"], "rss")
        self.assertEqual(self.store.search_items(query="美联储")[0]["source_type"], "json")


if __name__ == "__main__":
    unittest.main()
