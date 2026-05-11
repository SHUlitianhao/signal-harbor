from __future__ import annotations

from datetime import datetime, timezone
import unittest

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from signal_harbor.quant import (
    compute_industry_domain_detail,
    compute_industry_domains,
    compute_related_stocks_for_domain,
    load_industry_domain_catalog,
    load_stock_universe,
)


class IndustryDomainScoringTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = load_industry_domain_catalog(ROOT / "config" / "industry_domains.example.json")
        self.now = datetime(2026, 5, 11, 12, 0, tzinfo=timezone.utc)

    def test_loads_default_industry_domain_catalog(self) -> None:
        self.assertGreaterEqual(len(self.catalog), 8)
        ai_domain = [item for item in self.catalog if item.id == "ai-compute-power"][0]
        self.assertIn("算力", ai_domain.match_terms)
        self.assertIn("订单", ai_domain.positive_keywords)

    def test_loads_default_stock_universe_for_all_domains(self) -> None:
        stocks = load_stock_universe(ROOT / "config" / "stock_universe.example.json")
        covered_domains = {domain_id for stock in stocks for domain_id in stock.domain_ids}

        self.assertGreaterEqual(len(stocks), 8)
        self.assertTrue({domain.id for domain in self.catalog}.issubset(covered_domains))
        first = stocks[0]
        self.assertTrue(first.code)
        self.assertTrue(first.name)
        self.assertTrue(first.notes)

    def test_scores_news_driven_hot_domain_with_evidence(self) -> None:
        items = [
            {
                "id": "item_ai_1",
                "event_key": "event_ai_capex",
                "title": "AI 数据中心资本开支推动光模块订单增长",
                "canonical_text": "海外 AI 资本开支提升，服务器和光模块订单增长。",
                "published_at": "2026-05-11T10:00:00+00:00",
                "source_name": "华尔街见闻重要快讯",
                "source_quality_tier": "rsshub",
                "source_url": "https://example.test/ai-1",
                "tags": ["AI", "光模块", "订单"],
                "risk_flags": [],
                "score": 72,
            },
            {
                "id": "item_ai_2",
                "event_key": "event_ai_capex",
                "title": "数据中心用电需求增长 电网投资受关注",
                "canonical_text": "数据中心用电需求增长，电网投资和液冷服务器成为观察点。",
                "published_at": "2026-05-11T10:30:00+00:00",
                "source_name": "财联社加红电报",
                "source_quality_tier": "rsshub",
                "source_url": "https://example.test/ai-2",
                "tags": ["数据中心", "电网", "需求增长"],
                "risk_flags": [],
                "score": 68,
            },
        ]

        domains = compute_industry_domains(items, self.catalog, window_days=7, now=self.now)
        top = domains[0]

        self.assertEqual(top["domain_id"], "ai-compute-power")
        self.assertGreater(top["attention_score"], 0)
        self.assertGreater(top["benefit_score"], 0)
        self.assertEqual(top["market_confirmation"], "未接入")
        self.assertEqual(top["event_count"], 1)
        self.assertEqual(top["source_count"], 2)
        self.assertEqual({ref["source_url"] for ref in top["evidence_refs"]}, {"https://example.test/ai-1", "https://example.test/ai-2"})
        self.assertIn("订单", top["main_catalysts"])
        self.assertGreater(top["related_stock_count"], 0)
        self.assertGreater(top["short_term_catalyst_score"], 0)
        self.assertGreater(top["continuity_score"], 0)
        self.assertIn("近 3 天", top["recommendation_reason"])

    def test_risk_keywords_raise_risk_score_without_losing_original_url(self) -> None:
        items = [
            {
                "id": "item_chip_risk",
                "event_key": "event_chip_risk",
                "title": "半导体出口管制升级 芯片设备需求下滑",
                "canonical_text": "半导体设备面临出口管制和需求下滑风险。",
                "published_at": "2026-05-11T08:00:00+00:00",
                "source_name": "金十重要快讯",
                "source_quality_tier": "rsshub",
                "source_url": "https://example.test/chip-risk",
                "tags": ["半导体", "芯片", "出口管制"],
                "risk_flags": ["制裁"],
                "score": 80,
            }
        ]

        detail = compute_industry_domain_detail("semiconductor-autonomy", items, self.catalog, window_days=7, now=self.now)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertGreater(detail["risk_score"], 0)
        self.assertIn("出口管制", detail["risk_flags"])
        self.assertEqual(detail["evidence_refs"][0]["source_url"], "https://example.test/chip-risk")

    def test_old_news_outside_window_does_not_support_hot_domain(self) -> None:
        items = [
            {
                "id": "item_old",
                "event_key": "event_old",
                "title": "机器人订单增长",
                "canonical_text": "人形机器人订单增长。",
                "published_at": "2026-04-01T08:00:00+00:00",
                "source_name": "旧来源",
                "source_url": "https://example.test/old",
                "tags": ["机器人"],
                "risk_flags": [],
            }
        ]

        domains = compute_industry_domains(items, self.catalog, window_days=7, now=self.now)

        self.assertEqual(domains, [])

    def test_weak_generic_terms_alone_do_not_recommend_domain(self) -> None:
        items = [
            {
                "id": "item_weak",
                "event_key": "event_weak",
                "title": "电影酒店消费增长",
                "canonical_text": "消费增长，酒店和电影相关消息更新。",
                "published_at": "2026-05-11T08:00:00+00:00",
                "source_name": "弱来源",
                "source_url": "https://example.test/weak",
                "tags": ["消费"],
                "risk_flags": [],
            }
        ]

        domains = compute_industry_domains(items, self.catalog, window_days=7, now=self.now)

        self.assertEqual([domain["domain_id"] for domain in domains], [])

    def test_sustained_hot_domain_can_stay_recommended(self) -> None:
        items = []
        for index, day in enumerate([1, 4, 8], start=1):
            items.append(
                {
                    "id": f"item_power_{index}",
                    "event_key": f"event_power_{index}",
                    "title": "特高压电网设备招标持续推进",
                    "canonical_text": "新型电力系统建设推进，特高压和电网设备招标需求增长。",
                    "published_at": f"2026-05-{11 - day:02d}T08:00:00+00:00",
                    "source_name": f"来源 {index}",
                    "source_url": f"https://example.test/power-{index}",
                    "tags": ["特高压", "电网设备", "招标"],
                    "risk_flags": [],
                }
            )

        domains = compute_industry_domains(items, self.catalog, window_days=14, now=self.now)
        power = [domain for domain in domains if domain["domain_id"] == "new-power-system"][0]

        self.assertGreaterEqual(power["sustained_event_count"], 3)
        self.assertGreater(power["continuity_score"], 0)
        self.assertIn("持续热点", power["recommendation_reason"])

    def test_related_stocks_are_ranked_with_reasons_and_evidence(self) -> None:
        items = [
            {
                "id": "item_stock_ai",
                "event_key": "event_stock_ai",
                "title": "中际旭创光模块订单受 AI 数据中心需求拉动",
                "canonical_text": "AI 数据中心资本开支提升，800G 光模块订单增长。",
                "published_at": "2026-05-11T10:00:00+00:00",
                "source_name": "华尔街见闻重要快讯",
                "source_quality_tier": "rsshub",
                "source_url": "https://example.test/stock-ai",
                "tags": ["AI", "光模块", "订单"],
                "risk_flags": [],
                "score": 88,
            },
            {
                "id": "item_stock_ai_risk",
                "event_key": "event_stock_ai",
                "title": "光模块交易拥挤风险升温",
                "canonical_text": "AI 算力链条短期交易拥挤。",
                "published_at": "2026-05-11T11:00:00+00:00",
                "source_name": "金十重要快讯",
                "source_quality_tier": "rsshub",
                "source_url": "https://example.test/stock-ai-risk",
                "tags": ["光模块", "AI"],
                "risk_flags": ["交易拥挤"],
                "score": 70,
            },
        ]
        domain = [item for item in self.catalog if item.id == "ai-compute-power"][0]
        detail = compute_industry_domain_detail(domain.id, items, self.catalog, window_days=7, now=self.now)

        self.assertIsNotNone(detail)
        assert detail is not None
        stocks = detail["related_stocks_top10"]
        self.assertLessEqual(len(stocks), 10)
        self.assertGreater(len(stocks), 0)
        self.assertEqual(stocks[0]["association_rank"], 1)
        self.assertGreaterEqual(stocks[0]["association_score"], stocks[-1]["association_score"])
        self.assertTrue(stocks[0]["match_reasons"])
        self.assertTrue(stocks[0]["evidence_refs"])
        self.assertIn("excess_return_validation", stocks[0]["monitoring_metrics"])
        self.assertIn("待验证", stocks[0]["monitoring_metrics"]["excess_return_validation"])
        self.assertIn("交易拥挤", json_text(stocks))

    def test_related_stocks_can_degrade_when_no_stock_config(self) -> None:
        domain = [item for item in self.catalog if item.id == "ai-compute-power"][0]
        detail = compute_industry_domain_detail(domain.id, [], self.catalog, window_days=7, now=self.now)
        stocks = compute_related_stocks_for_domain(domain, [], detail or {}, stock_universe=[], now=self.now)

        self.assertEqual(stocks, [])


def json_text(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
