from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_INDUSTRY_DOMAIN_CONFIG = Path(__file__).resolve().parents[3] / "config" / "industry_domains.example.json"
DEFAULT_STOCK_UNIVERSE_CONFIG = Path(__file__).resolve().parents[3] / "config" / "stock_universe.example.json"

SOURCE_QUALITY_WEIGHTS = {
    "official": 1.3,
    "exchange": 1.2,
    "rsshub": 1.1,
    "media": 1.0,
    "blog": 0.8,
    "forum": 0.7,
}
FRESH_CATALYST_DAYS = 3
SUSTAINED_HOT_DAYS = 14
WEAK_DOMAIN_TERMS = {
    "增长",
    "投资",
    "发布",
    "政策支持",
    "需求增长",
    "消费",
    "酒店",
    "电影",
    "旅游",
    "订单",
    "风险",
    "市场",
    "出口",
}


@dataclass(frozen=True)
class IndustryDomainConfig:
    id: str
    name: str
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    concept_tags: list[str] = field(default_factory=list)
    sw_l1_list: list[str] = field(default_factory=list)
    sw_l2_list: list[str] = field(default_factory=list)
    sw_l3_list: list[str] = field(default_factory=list)
    positive_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    next_observation_points: list[str] = field(default_factory=list)

    @property
    def match_terms(self) -> list[str]:
        return _dedupe(
            self.keywords
            + self.concept_tags
            + self.sw_l1_list
            + self.sw_l2_list
            + self.sw_l3_list
        )


@dataclass(frozen=True)
class StockUniverseConfig:
    code: str
    name: str
    exchange: str = ""
    sw_l1: str = ""
    sw_l2: str = ""
    sw_l3: str = ""
    concept_tags: list[str] = field(default_factory=list)
    business_keywords: list[str] = field(default_factory=list)
    domain_ids: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def match_terms(self) -> list[str]:
        return _dedupe(
            [self.name, self.sw_l1, self.sw_l2, self.sw_l3]
            + self.concept_tags
            + self.business_keywords
            + self.domain_ids
        )


def load_industry_domain_catalog(path: str | Path | None = None) -> list[IndustryDomainConfig]:
    config_path = Path(path) if path else DEFAULT_INDUSTRY_DOMAIN_CONFIG
    data = json.loads(config_path.read_text(encoding="utf-8"))
    raw_domains = data if isinstance(data, list) else data.get("domains", [])
    domains = []
    for raw in raw_domains:
        domains.append(
            IndustryDomainConfig(
                id=str(raw.get("id") or _slugify(str(raw.get("domain_name") or raw.get("name") or ""))),
                name=str(raw.get("domain_name") or raw.get("name") or ""),
                description=str(raw.get("domain_description") or raw.get("description") or ""),
                keywords=_string_list(raw.get("evidence_keywords") or raw.get("keywords")),
                concept_tags=_string_list(raw.get("concept_tags")),
                sw_l1_list=_string_list(raw.get("sw_l1_list")),
                sw_l2_list=_string_list(raw.get("sw_l2_list")),
                sw_l3_list=_string_list(raw.get("sw_l3_list")),
                positive_keywords=_string_list(raw.get("positive_keywords")),
                negative_keywords=_string_list(raw.get("negative_keywords")),
                next_observation_points=_string_list(raw.get("next_observation_points")),
            )
        )
    return [domain for domain in domains if domain.id and domain.name]


def load_stock_universe(path: str | Path | None = None) -> list[StockUniverseConfig]:
    config_path = Path(path) if path else DEFAULT_STOCK_UNIVERSE_CONFIG
    if not config_path.exists():
        return []
    data = json.loads(config_path.read_text(encoding="utf-8"))
    raw_stocks = data if isinstance(data, list) else data.get("stocks", [])
    stocks = []
    for raw in raw_stocks:
        stocks.append(
            StockUniverseConfig(
                code=str(raw.get("stock_code") or raw.get("code") or "").strip(),
                name=str(raw.get("stock_name") or raw.get("name") or "").strip(),
                exchange=str(raw.get("exchange") or "").strip(),
                sw_l1=str(raw.get("sw_l1") or "").strip(),
                sw_l2=str(raw.get("sw_l2") or "").strip(),
                sw_l3=str(raw.get("sw_l3") or "").strip(),
                concept_tags=_string_list(raw.get("concept_tags")),
                business_keywords=_string_list(raw.get("business_keywords")),
                domain_ids=_string_list(raw.get("domain_ids")),
                notes=str(raw.get("notes") or "").strip(),
            )
        )
    return [stock for stock in stocks if stock.code and stock.name]


def compute_industry_domains(
    items: list[dict[str, Any]],
    catalog: list[IndustryDomainConfig],
    window_days: int = 7,
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    evaluated_at = now or datetime.now(timezone.utc)
    clean_window = max(1, min(int(window_days or 7), 90))
    results = [
        _score_domain(domain, items, clean_window, evaluated_at)
        for domain in catalog
    ]
    visible = [item for item in results if _is_recommendable_domain(item)]
    visible.sort(
        key=lambda item: (
            float(item["domain_score"]),
            float(item["attention_score"]),
            int(item["evidence_count"]),
        ),
        reverse=True,
    )
    for index, item in enumerate(visible, start=1):
        item["rank"] = index
        item["related_stock_count"] = _related_stock_config_count(item["domain_id"])
    return visible[: max(1, min(int(limit or 5), 20))]


def compute_industry_domain_detail(
    domain_id: str,
    items: list[dict[str, Any]],
    catalog: list[IndustryDomainConfig],
    window_days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    for domain in catalog:
        if domain.id == domain_id:
            detail = _score_domain(domain, items, max(1, min(int(window_days or 7), 90)), now or datetime.now(timezone.utc))
            detail["rank"] = 0
            stocks = compute_related_stocks_for_domain(domain, items, detail, now=now)
            detail["related_stocks_top10"] = stocks
            detail["related_stock_count"] = len(stocks)
            detail["related_stock_status"] = "可用" if stocks else "暂无可匹配的监控样本，需补充股票池配置或等待更多新闻证据。"
            return detail
    return None


def compute_related_stocks_for_domain(
    domain: IndustryDomainConfig,
    items: list[dict[str, Any]],
    domain_result: dict[str, Any],
    stock_universe: list[StockUniverseConfig] | None = None,
    now: datetime | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    stocks = stock_universe if stock_universe is not None else load_stock_universe()
    if not stocks:
        return []
    evaluated_at = now or datetime.now(timezone.utc)
    scored = [
        _score_related_stock(domain, stock, items, domain_result, evaluated_at)
        for stock in stocks
    ]
    visible = [item for item in scored if item["association_score"] > 0]
    visible.sort(
        key=lambda item: (
            float(item["association_score"]),
            len(item["evidence_refs"]),
            len(item["related_events"]),
            item["stock_code"],
        ),
        reverse=True,
    )
    for index, item in enumerate(visible[: max(1, min(int(limit or 10), 10))], start=1):
        item["association_rank"] = index
    return visible[: max(1, min(int(limit or 10), 10))]


def _score_domain(
    domain: IndustryDomainConfig,
    items: list[dict[str, Any]],
    window_days: int,
    now: datetime,
) -> dict[str, Any]:
    current_start = now - timedelta(days=window_days)
    previous_start = now - timedelta(days=window_days * 2)
    matches: list[dict[str, Any]] = []
    previous_events: set[str] = set()
    for item in items:
        published_at = _parse_time(str(item.get("published_at") or item.get("created_at") or ""))
        if not published_at:
            continue
        matched_terms = _matched_terms(_item_search_text(item), domain.match_terms)
        if not matched_terms:
            continue
        enriched = dict(item)
        enriched["_published_at_dt"] = published_at
        enriched["_matched_terms"] = matched_terms
        if published_at >= current_start:
            matches.append(enriched)
        elif previous_start <= published_at < current_start:
            previous_events.add(_event_key(enriched))

    event_keys = {_event_key(item) for item in matches}
    fresh_start = now - timedelta(days=FRESH_CATALYST_DAYS)
    sustained_start = now - timedelta(days=SUSTAINED_HOT_DAYS)
    fresh_matches = [item for item in matches if item["_published_at_dt"] >= fresh_start]
    sustained_matches = [item for item in matches if item["_published_at_dt"] >= sustained_start]
    fresh_event_keys = {_event_key(item) for item in fresh_matches}
    sustained_event_keys = {_event_key(item) for item in sustained_matches}
    sources = _dedupe(str(item.get("source_name") or item.get("source_id") or "未知渠道") for item in matches)
    fresh_sources = _dedupe(str(item.get("source_name") or item.get("source_id") or "未知渠道") for item in fresh_matches)
    sustained_sources = _dedupe(str(item.get("source_name") or item.get("source_id") or "未知渠道") for item in sustained_matches)
    source_quality_score = _source_quality_score(matches)
    event_count_score = min(25.0, _event_count_points(len(sustained_event_keys)))
    velocity_score = _velocity_points(len(fresh_event_keys), len(previous_events))
    source_breadth_score = _source_breadth_points(len(sources))
    confirmation_score = _confirmation_points(matches)
    recency_score = _recency_points(matches, now)

    positive_evidence = _keyword_evidence(matches, domain.positive_keywords)
    negative_evidence = _keyword_evidence(matches, domain.negative_keywords)
    fresh_positive_evidence = _keyword_evidence(fresh_matches, domain.positive_keywords)
    non_weak_keywords = sorted({term for item in matches for term in item["_matched_terms"] if term not in WEAK_DOMAIN_TERMS})
    weak_only = bool(matches and not non_weak_keywords)
    short_term_catalyst_score = round(
        min(
            60.0,
            _event_count_points(len(fresh_event_keys))
            + _source_breadth_points(len(fresh_sources))
            + len(fresh_positive_evidence["terms"]) * 5.0
            + _confirmation_points(fresh_matches)
            + _source_quality_score(fresh_matches) * 0.5
            + _recency_points(fresh_matches, now),
        ),
        1,
    )
    continuity_score = round(
        min(
            40.0,
            _event_count_points(len(sustained_event_keys)) * 0.7
            + _source_breadth_points(len(sustained_sources))
            + _confirmation_points(sustained_matches)
            + _source_quality_score(sustained_matches) * 0.4,
        ),
        1,
    )
    noise_penalty = _noise_penalty(matches, domain, weak_only=weak_only)
    attention_score = round(min(100.0, short_term_catalyst_score + continuity_score), 1)
    benefit_score = round(min(50.0, len(positive_evidence["terms"]) * 5.0 + positive_evidence["event_count"] * 3.0 + confirmation_score * 0.4), 1)
    risk_score = round(min(50.0, len(negative_evidence["terms"]) * 5.0 + negative_evidence["event_count"] * 3.0), 1)
    domain_score = round(max(0.0, short_term_catalyst_score + continuity_score + benefit_score * 0.5 - risk_score * 0.8 - noise_penalty), 1)
    direction, level = _signal_labels(attention_score, benefit_score, risk_score)
    evidence_refs = _evidence_refs(matches)
    related_events = _related_events(matches)
    recommendation_reason = _recommendation_reason(
        short_term_catalyst_score,
        continuity_score,
        fresh_event_count=len(fresh_event_keys),
        sustained_event_count=len(sustained_event_keys),
        source_count=len(sources),
        catalysts=positive_evidence["terms"],
        noise_penalty=noise_penalty,
    )

    return {
        "domain_id": domain.id,
        "domain_name": domain.name,
        "domain_description": domain.description,
        "rank": 0,
        "attention_score": attention_score,
        "benefit_score": benefit_score,
        "risk_score": risk_score,
        "domain_score": domain_score,
        "signal_direction": direction,
        "signal_level": level,
        "main_catalysts": positive_evidence["terms"][:8],
        "risk_flags": negative_evidence["terms"][:8],
        "matched_keywords": sorted({term for item in matches for term in item["_matched_terms"]})[:12],
        "source_count": len(sources),
        "event_count": len(event_keys),
        "evidence_count": len(evidence_refs),
        "related_events": related_events[:8],
        "positive_evidence": positive_evidence["refs"][:8],
        "negative_evidence": negative_evidence["refs"][:8],
        "evidence_refs": evidence_refs[:20],
        "next_observation_points": domain.next_observation_points[:8],
        "market_confirmation": "未接入",
        "related_stock_count": _related_stock_config_count(domain.id),
        "recommendation_reason": recommendation_reason,
        "short_term_catalyst_score": short_term_catalyst_score,
        "continuity_score": continuity_score,
        "noise_penalty": round(noise_penalty, 1),
        "fresh_event_count": len(fresh_event_keys),
        "sustained_event_count": len(sustained_event_keys),
        "score_explanation": {
            "event_count_score": round(event_count_score, 1),
            "velocity_score": round(velocity_score, 1),
            "source_breadth_score": round(source_breadth_score, 1),
            "source_quality_score": round(source_quality_score, 1),
            "same_event_confirmation_score": round(confirmation_score, 1),
            "recency_score": round(recency_score, 1),
            "short_term_catalyst_score": short_term_catalyst_score,
            "continuity_score": continuity_score,
            "noise_penalty": round(noise_penalty, 1),
            "window_days": window_days,
        },
        "updated_at": now.replace(microsecond=0).isoformat(),
    }


def _score_related_stock(
    domain: IndustryDomainConfig,
    stock: StockUniverseConfig,
    items: list[dict[str, Any]],
    domain_result: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    direct_domain_match = domain.id in stock.domain_ids
    matched_industry_tags = _dedupe(
        [value for value in [stock.sw_l1, stock.sw_l2, stock.sw_l3] if value in domain.match_terms]
    )
    matched_concepts = _dedupe([tag for tag in stock.concept_tags if tag in domain.match_terms])
    matched_keywords = _dedupe([term for term in stock.business_keywords if term in domain.match_terms])
    domain_evidence_terms = _dedupe(domain_result.get("matched_keywords", []) + domain_result.get("main_catalysts", []))
    matched_keywords = _dedupe(matched_keywords + [term for term in stock.business_keywords if term in domain_evidence_terms])
    matched_concepts = _dedupe(matched_concepts + [tag for tag in stock.concept_tags if tag in domain_evidence_terms])

    stock_terms = _dedupe([stock.name] + stock.concept_tags + stock.business_keywords + [stock.sw_l1, stock.sw_l2, stock.sw_l3])
    evidence_refs = _stock_evidence_refs(items, stock_terms)
    related_events = _stock_related_events(items, stock_terms)
    risk_terms = _stock_risk_terms(items, stock_terms, domain.negative_keywords)
    recency_bonus = _stock_recency_bonus(items, stock_terms, now)
    source_quality = _source_quality_score([item for item in items if _matched_terms(_item_search_text(item), stock_terms)])

    score = 0.0
    if direct_domain_match:
        score += 30.0
    score += min(18.0, len(matched_industry_tags) * 6.0)
    score += min(18.0, len(matched_concepts) * 4.5)
    score += min(18.0, len(matched_keywords) * 3.0)
    score += min(12.0, len({ref.get("event_key") for ref in evidence_refs}) * 4.0)
    score += min(8.0, len({ref.get("source_name") for ref in evidence_refs}) * 2.0)
    score += min(8.0, source_quality * 0.5)
    score += recency_bonus
    score -= min(18.0, len(risk_terms) * 4.0)
    association_score = round(max(0.0, min(100.0, score)), 1)

    match_reasons = _stock_match_reasons(
        direct_domain_match=direct_domain_match,
        industry_tags=matched_industry_tags,
        concepts=matched_concepts,
        keywords=matched_keywords,
        evidence_count=len(evidence_refs),
        risk_terms=risk_terms,
    )
    monitoring_metrics = {
        "market_data": "未接入",
        "capital_flow": "未接入",
        "financials": "未接入",
        "valuation": "未接入",
        "excess_return_validation": "待验证",
    }
    return {
        "stock_code": stock.code,
        "stock_name": stock.name,
        "exchange": stock.exchange,
        "association_score": association_score,
        "association_rank": 0,
        "match_reasons": match_reasons,
        "matched_concepts": matched_concepts[:8],
        "matched_industry_tags": matched_industry_tags[:8],
        "matched_keywords": matched_keywords[:8],
        "related_events": related_events[:5],
        "evidence_refs": evidence_refs[:8] or list(domain_result.get("evidence_refs", []))[:3],
        "monitoring_metrics": monitoring_metrics,
        "updated_at": now.replace(microsecond=0).isoformat(),
        "notes": stock.notes,
        "research_role": "监控样本",
        "risk_deductions": risk_terms[:8],
    }


def _related_stock_config_count(domain_id: str) -> int:
    return len([stock for stock in load_stock_universe() if domain_id in stock.domain_ids])


def _is_recommendable_domain(domain: dict[str, Any]) -> bool:
    fresh = int(domain.get("fresh_event_count") or 0)
    sustained = int(domain.get("sustained_event_count") or 0)
    sources = int(domain.get("source_count") or 0)
    short_score = float(domain.get("short_term_catalyst_score") or 0)
    continuity = float(domain.get("continuity_score") or 0)
    catalysts = len(domain.get("main_catalysts") or [])
    if fresh >= 2 and short_score >= 25:
        return True
    if fresh >= 1 and sources >= 2 and catalysts >= 1:
        return True
    if sustained >= 3 and sources >= 2 and continuity >= 20:
        return True
    return bool(domain.get("evidence_count") and float(domain.get("domain_score") or 0) >= 45)


def _noise_penalty(items: list[dict[str, Any]], domain: IndustryDomainConfig, *, weak_only: bool) -> float:
    if not items:
        return 0.0
    penalty = 0.0
    if weak_only:
        penalty += 18.0
    matched_terms = [term for item in items for term in item.get("_matched_terms", [])]
    weak_hits = [term for term in matched_terms if term in WEAK_DOMAIN_TERMS]
    if weak_hits and len(weak_hits) >= max(3, len(matched_terms) * 0.6):
        penalty += 8.0
    event_keys = {_event_key(item) for item in items}
    source_count = len(_dedupe(str(item.get("source_name") or item.get("source_id") or "") for item in items))
    if len(items) >= 8 and source_count <= 1:
        penalty += 8.0
    if len(event_keys) <= 1 and len(items) >= 6:
        penalty += 6.0
    core_terms = set(domain.concept_tags + domain.sw_l2_list + domain.sw_l3_list)
    if core_terms and not (set(matched_terms) & core_terms):
        penalty += 5.0
    return min(35.0, penalty)


def _recommendation_reason(
    short_term_score: float,
    continuity_score: float,
    *,
    fresh_event_count: int,
    sustained_event_count: int,
    source_count: int,
    catalysts: list[str],
    noise_penalty: float,
) -> str:
    reasons = []
    if short_term_score >= 25:
        reasons.append(f"近 {FRESH_CATALYST_DAYS} 天 {fresh_event_count} 个强催化事件")
    if continuity_score >= 20:
        reasons.append(f"近 {SUSTAINED_HOT_DAYS} 天 {sustained_event_count} 个持续热点事件")
    if source_count >= 2:
        reasons.append(f"{source_count} 个来源交叉确认")
    if catalysts:
        reasons.append(f"催化词：{'、'.join(catalysts[:3])}")
    if noise_penalty:
        reasons.append(f"噪声扣分 {round(noise_penalty, 1)}")
    return "；".join(reasons) or "暂未形成足够强的推荐理由"


def _stock_evidence_refs(items: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    refs = []
    for item in items:
        matched = _matched_terms(_item_search_text(item), terms)
        if matched:
            refs.append(_item_evidence_ref(item, matched_terms=matched))
    refs.sort(key=lambda ref: str(ref.get("published_at") or ""), reverse=True)
    return refs


def _stock_related_events(items: list[dict[str, Any]], terms: list[str]) -> list[dict[str, Any]]:
    related_items = []
    for item in items:
        matched = _matched_terms(_item_search_text(item), terms)
        if matched:
            enriched = dict(item)
            enriched["_matched_terms"] = matched
            related_items.append(enriched)
    return _related_events(related_items)


def _stock_risk_terms(items: list[dict[str, Any]], stock_terms: list[str], risk_terms: list[str]) -> list[str]:
    risks = set()
    for item in items:
        text = _item_search_text(item)
        if _matched_terms(text, stock_terms):
            risks.update(_matched_terms(text, risk_terms))
            risks.update(_string_list(item.get("risk_flags")))
    return sorted(risks)


def _stock_recency_bonus(items: list[dict[str, Any]], terms: list[str], now: datetime) -> float:
    matched_times = []
    for item in items:
        if _matched_terms(_item_search_text(item), terms):
            parsed = _parse_time(str(item.get("published_at") or item.get("created_at") or ""))
            if parsed:
                matched_times.append(parsed)
    if not matched_times:
        return 0.0
    newest = max(matched_times)
    age_hours = max(0.0, (now - newest).total_seconds() / 3600)
    if age_hours <= 24:
        return 6.0
    if age_hours <= 72:
        return 4.0
    if age_hours <= 168:
        return 2.0
    return 0.0


def _stock_match_reasons(
    *,
    direct_domain_match: bool,
    industry_tags: list[str],
    concepts: list[str],
    keywords: list[str],
    evidence_count: int,
    risk_terms: list[str],
) -> list[str]:
    reasons = []
    if direct_domain_match:
        reasons.append("股票池配置明确归属该行业域")
    if industry_tags:
        reasons.append(f"申万行业匹配：{'、'.join(industry_tags[:4])}")
    if concepts:
        reasons.append(f"概念标签匹配：{'、'.join(concepts[:4])}")
    if keywords:
        reasons.append(f"业务关键词命中：{'、'.join(keywords[:4])}")
    if evidence_count:
        reasons.append(f"近期新闻/事件证据 {evidence_count} 条")
    if risk_terms:
        reasons.append(f"存在风险扣分：{'、'.join(risk_terms[:4])}")
    return reasons or ["仅作为备选监控样本，等待更多公开证据确认"]


def _item_search_text(item: dict[str, Any]) -> str:
    parts = [
        item.get("title", ""),
        item.get("canonical_text", ""),
        item.get("summary", ""),
        " ".join(_string_list(item.get("tags"))),
        " ".join(_string_list(item.get("entities"))),
        " ".join(_string_list(item.get("risk_flags"))),
    ]
    translation = item.get("translation") or {}
    if isinstance(translation, dict):
        parts.extend(
            [
                translation.get("translated_title", ""),
                translation.get("translated_summary", ""),
                " ".join(_string_list(translation.get("translated_tags"))),
                " ".join(_string_list(translation.get("translated_risk_flags"))),
            ]
        )
    return "\n".join(str(part) for part in parts if part)


def _matched_terms(text: str, terms: list[str]) -> list[str]:
    normalized = text.lower()
    return [term for term in terms if term and term.lower() in normalized]


def _keyword_evidence(items: list[dict[str, Any]], keywords: list[str]) -> dict[str, Any]:
    refs = []
    terms: set[str] = set()
    event_keys: set[str] = set()
    for item in items:
        matched = _matched_terms(_item_search_text(item), keywords)
        if not matched:
            continue
        terms.update(matched)
        event_keys.add(_event_key(item))
        refs.append(_item_evidence_ref(item, matched_terms=matched))
    return {"terms": sorted(terms), "event_count": len(event_keys), "refs": refs}


def _evidence_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = [_item_evidence_ref(item, matched_terms=item.get("_matched_terms", [])) for item in items]
    refs.sort(key=lambda ref: str(ref.get("published_at") or ""), reverse=True)
    return refs


def _related_events(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        grouped.setdefault(_event_key(item), []).append(item)
    events = []
    for event_key, group in grouped.items():
        group.sort(key=lambda item: str(item.get("published_at") or ""), reverse=True)
        primary = group[0]
        events.append(
            {
                "event_key": event_key,
                "title": primary.get("title", ""),
                "source_count": len(_dedupe(str(item.get("source_name") or item.get("source_id") or "未知渠道") for item in group)),
                "item_count": len(group),
                "latest_at": primary.get("published_at", ""),
                "score": max(float(item.get("score") or 0) for item in group),
                "matched_keywords": sorted({term for item in group for term in item.get("_matched_terms", [])})[:8],
            }
        )
    events.sort(key=lambda item: str(item.get("latest_at") or ""), reverse=True)
    return events


def _item_evidence_ref(item: dict[str, Any], matched_terms: list[str]) -> dict[str, Any]:
    return {
        "item_id": item.get("id", ""),
        "event_key": item.get("event_key", ""),
        "title": item.get("title", ""),
        "source_name": item.get("source_name") or item.get("source_id") or "未知渠道",
        "source_url": item.get("source_url", ""),
        "published_at": item.get("published_at", ""),
        "score": item.get("score", 0),
        "matched_terms": list(matched_terms)[:8],
    }


def _event_key(item: dict[str, Any]) -> str:
    return str(item.get("event_key") or item.get("id") or item.get("canonical_hash") or item.get("source_url") or "")


def _event_count_points(count: int) -> float:
    if count <= 0:
        return 0.0
    if count <= 2:
        return 5.0
    if count <= 5:
        return 10.0
    if count <= 10:
        return 18.0
    return 25.0


def _velocity_points(current: int, previous: int) -> float:
    if current <= 0:
        return 0.0
    if previous <= 0:
        return 20.0 if current >= 3 else 10.0
    ratio = current / max(1, previous)
    if ratio < 1.0:
        return 0.0
    if ratio < 1.5:
        return 5.0
    if ratio < 2.5:
        return 10.0
    if ratio < 4.0:
        return 15.0
    return 20.0


def _source_breadth_points(count: int) -> float:
    if count <= 0:
        return 0.0
    if count == 1:
        return 3.0
    if count <= 3:
        return 8.0
    if count <= 6:
        return 12.0
    return 15.0


def _source_quality_score(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    weights = []
    for item in items:
        tier = str(item.get("source_quality_tier") or (item.get("metadata") or {}).get("quality_tier") or item.get("source_type") or "").lower()
        weights.append(SOURCE_QUALITY_WEIGHTS.get(tier, 1.0))
    return min(15.0, 8.0 * (sum(weights) / max(1, len(weights))))


def _confirmation_points(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    counts = Counter(_event_key(item) for item in items)
    max_sources = 1
    for event_key in counts:
        event_items = [item for item in items if _event_key(item) == event_key]
        max_sources = max(max_sources, len(_dedupe(str(item.get("source_name") or item.get("source_id") or "") for item in event_items)))
    if max_sources <= 1:
        return 3.0
    if max_sources <= 3:
        return 6.0
    return 15.0


def _recency_points(items: list[dict[str, Any]], now: datetime) -> float:
    if not items:
        return 0.0
    newest = max(item["_published_at_dt"] for item in items)
    age_hours = max(0.0, (now - newest).total_seconds() / 3600)
    if age_hours <= 24:
        return 10.0
    if age_hours <= 72:
        return 8.0
    if age_hours <= 168:
        return 6.0
    if age_hours <= 720:
        return 3.0
    return 0.0


def _signal_labels(attention: float, benefit: float, risk: float) -> tuple[str, str]:
    if attention >= 70 and benefit > risk + 15:
        return "利好", "重点研究"
    if attention >= 70 and risk > benefit + 15:
        return "利空", "风险较高"
    if attention >= 70:
        return "仅热门", "仅热门待确认"
    if attention >= 40 and benefit >= risk:
        return "中性偏强", "继续观察"
    if attention >= 40:
        return "中性偏弱", "继续观察"
    return "中性", "暂无明显信号"


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _dedupe(values: Any) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return normalized or f"domain-{abs(hash(value)) % 100000}"
