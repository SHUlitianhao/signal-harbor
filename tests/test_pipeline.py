from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.adapters import FixtureSourceAdapter
from signal_harbor.adapters.base import RawContent
from signal_harbor.core import IngestPipeline
from signal_harbor.domain import Favorite, Source
from signal_harbor.storage import SQLiteStore


class FailingAdapter:
    source = Source(
        id="src_failing",
        name="失败 fixture",
        source_type="fixture",
        location="memory://failing",
    )

    def collect(self) -> list[RawContent]:
        raise RuntimeError("simulated source failure")


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = SQLiteStore(self.root / "test.sqlite3")
        self.fixture_path = self.root / "items.json"

    def tearDown(self) -> None:
        self.store.close()
        self.tempdir.cleanup()

    def write_fixture(self, items: list[dict[str, object]]) -> None:
        self.fixture_path.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    def test_pipeline_normalizes_deduplicates_and_creates_evidence(self) -> None:
        self.write_fixture(
            [
                {
                    "source_url": " https://example.test/a ",
                    "title": "  AI   芯片 风险  ",
                    "text": " 第一行  \n\n 第二行包含监管风险和 AI 芯片 ",
                    "published_at": "2026-04-30T09:00:00+08:00",
                    "tags": ["AI", " AI "],
                    "assets": [{"type": "image", "url": "https://example.test/a.png"}],
                },
                {
                    "source_url": "https://example.test/a",
                    "title": "AI 芯片 风险",
                    "text": "第一行\n第二行包含监管风险和 AI 芯片",
                    "published_at": "2026-04-30T09:00:00+08:00",
                    "tags": ["AI"],
                    "assets": [],
                },
            ]
        )

        run = IngestPipeline(self.store).run_adapter(FixtureSourceAdapter(self.fixture_path))

        self.assertEqual(run.status, "success")
        self.assertEqual(run.items_found, 2)
        self.assertEqual(run.items_created, 1)
        item = self.store.list_latest_items()[0]
        self.assertEqual(item["title"], "AI 芯片 风险")
        self.assertIn("第二行包含监管风险和 AI 芯片", item["canonical_text"])
        self.assertIn("AI", item["entities"])
        self.assertGreaterEqual(item["score"], 60)

        detail = self.store.get_item(item["id"])
        self.assertIsNotNone(detail)
        self.assertEqual(len(detail["assets"]), 1)
        self.assertEqual(detail["insight"]["evidence_refs"][0]["url"], "https://example.test/a")
        self.assertIn("监管", detail["insight"]["risk_flags"])

    def test_pipeline_keeps_canonical_hash_deduplication_before_event_grouping(self) -> None:
        self.write_fixture(
            [
                {
                    "source_url": "https://example.test/same",
                    "title": "完全相同新闻",
                    "text": "完全相同正文包含监管风险。",
                    "published_at": "2026-05-09T09:00:00+08:00",
                    "tags": ["监管"],
                },
                {
                    "source_url": "https://example.test/same",
                    "title": "完全相同新闻",
                    "text": "完全相同正文包含监管风险。",
                    "published_at": "2026-05-09T09:00:00+08:00",
                    "tags": ["监管"],
                },
            ]
        )

        run = IngestPipeline(self.store).run_adapter(FixtureSourceAdapter(self.fixture_path))
        latest = self.store.list_latest_items()

        self.assertEqual(run.items_created, 1)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["related_count"], 0)
        self.assertTrue(latest[0]["event_key"])
        self.assertEqual(latest[0]["matched_tokens"], [])
        self.assertEqual(latest[0]["matched_topics"], [])
        self.assertEqual(latest[0]["time_window"]["hours"], 36)
        self.assertFalse(latest[0]["conflict_guard"]["blocked"])

    def test_search_filter_and_favorites(self) -> None:
        self.write_fixture(
            [
                {
                    "source_url": "https://example.test/regulation",
                    "title": "监管更新",
                    "text": "监管细则带来风险控制要求",
                    "published_at": "2026-04-30T10:00:00+08:00",
                    "tags": ["监管"],
                },
                {
                    "source_url": "https://example.test/gold",
                    "title": "黄金走势",
                    "text": "黄金和美元出现分歧",
                    "published_at": "2026-04-30T11:00:00+08:00",
                    "tags": ["黄金"],
                },
            ]
        )
        IngestPipeline(self.store).run_adapter(FixtureSourceAdapter(self.fixture_path))

        regulation = self.store.search_items(query="监管")
        self.assertEqual(len(regulation), 1)
        self.assertEqual(regulation[0]["title"], "监管更新")

        self.store.add_favorite(Favorite(item_id=regulation[0]["id"], note="重点跟踪"))
        favorites = self.store.search_items(favorite=True)
        self.assertEqual(len(favorites), 1)
        self.assertEqual(self.store.list_favorites()[0]["note"], "重点跟踪")

    def test_failed_source_does_not_block_other_sources(self) -> None:
        self.write_fixture(
            [
                {
                    "source_url": "https://example.test/ok",
                    "title": "正常来源",
                    "text": "AI 芯片订单更新",
                    "published_at": "2026-04-30T12:00:00+08:00",
                    "tags": ["AI"],
                }
            ]
        )
        pipeline = IngestPipeline(self.store)

        runs = pipeline.run_adapters([FailingAdapter(), FixtureSourceAdapter(self.fixture_path)])

        self.assertEqual([run.status for run in runs], ["failed", "success"])
        self.assertEqual(self.store.health()["items"], 1)
        task_runs = self.store.list_task_runs()
        self.assertEqual({task["status"] for task in task_runs}, {"failed", "success"})
        failed_source = self.store.get_source("src_failing")
        self.assertIn("simulated source failure", failed_source["last_error"])


if __name__ == "__main__":
    unittest.main()
