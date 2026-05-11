from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable

from signal_harbor.domain import stable_hash


EVENT_CONTEXT_LIMIT = 200
EVENT_ITEM_LIST_LIMIT = 3
EVENT_ITEM_DETAIL_LIMIT = 20
EVENT_EVIDENCE_LIST_LIMIT = 3
EVENT_EVIDENCE_DETAIL_LIMIT = 20
EVENT_RELATED_ITEM_LIMIT = 8
EVENT_TIME_WINDOW_SECONDS = 36 * 60 * 60
EVENT_STOPWORDS = {
    "rsshub",
    "快讯",
    "重要",
    "要闻",
    "最新",
    "实时",
    "新闻",
    "消息",
    "报道",
    "财经",
    "市场",
    "今日",
    "今天",
    "表示",
    "发布",
    "更新",
    "相关",
    "关于",
    "公司",
    "风险",
    "高价值线索",
    "公开页",
    "论坛",
    "html",
    "fixture",
    "重要快",
    "要快",
    "要快讯",
    "重要快讯",
    "重要要",
    "重要要闻",
    "国际新闻",
    "source",
    "news",
    "update",
    "market",
    "live",
    "important",
}
EVENT_CONFLICT_PAIRS = (
    ("加息", "降息"),
    ("上涨", "下跌"),
    ("上调", "下调"),
    ("增持", "减持"),
    ("买入", "卖出"),
    ("批准", "否决"),
    ("盈利", "亏损"),
    ("扩产", "裁员"),
)
EVENT_CONFLICT_LABELS = [f"{left}/{right}" for left, right in EVENT_CONFLICT_PAIRS]

InsightLookup = Callable[[str], dict[str, Any] | None]


def decorate_event_groups(
    items: list[dict[str, Any]],
    insight_lookup: InsightLookup,
    collapse: bool = False,
) -> list[dict[str, Any]]:
    groups = event_groups_for_items(items, insight_lookup)
    if collapse:
        return [group[0] for group in groups if group]
    return [item for group in groups for item in group]


def event_groups_for_items(
    items: list[dict[str, Any]],
    insight_lookup: InsightLookup,
) -> list[list[dict[str, Any]]]:
    if not items:
        return []
    groups: list[list[dict[str, Any]]] = []
    assigned: set[str] = set()
    lookup = _cached_insight_lookup(insight_lookup)
    for item in items:
        item_id = str(item.get("id", ""))
        if item_id in assigned:
            continue
        group = [item]
        assigned.add(item_id)
        for candidate in items:
            candidate_id = str(candidate.get("id", ""))
            if not candidate_id or candidate_id in assigned:
                continue
            if items_in_same_event(item, candidate):
                group.append(candidate)
                assigned.add(candidate_id)
        apply_event_group(group, lookup)
        groups.append(group)
    return groups


def decorate_single_item_event(
    item: dict[str, Any],
    candidates: list[dict[str, Any]],
    insight_lookup: InsightLookup,
) -> dict[str, Any]:
    related = [
        candidate
        for candidate in candidates
        if candidate.get("id") != item.get("id") and items_in_same_event(item, candidate)
    ]
    apply_event_group([item, *related], _cached_insight_lookup(insight_lookup), primary=item)
    return item


def event_group_payload(
    group: list[dict[str, Any]],
    insight_lookup: InsightLookup,
    item_limit: int | None = None,
    evidence_limit: int | None = None,
) -> dict[str, Any]:
    if not group:
        return {}
    lookup = _cached_insight_lookup(insight_lookup)
    primary = group[0]
    if "event_key" not in primary:
        apply_event_group(group, lookup)
    items = [_event_item_summary(item, lookup) for item in group]
    visible_items = items[:item_limit] if item_limit is not None else items
    related_items = visible_items[1:]
    evidence_refs = primary.get("event_evidence_refs", [])
    visible_evidence = evidence_refs[:evidence_limit] if evidence_limit is not None else evidence_refs
    return {
        "event_key": primary.get("event_key", ""),
        "event_group": primary.get("event_group", {}),
        "title": primary.get("title", ""),
        "primary_item_id": primary.get("id", ""),
        "item_count": len(group),
        "related_count": max(0, len(group) - 1),
        "related_items": related_items,
        "event_items": visible_items,
        "source_count": primary.get("source_count", 0),
        "event_sources": primary.get("event_sources", []),
        "event_latest_at": primary.get("event_latest_at", ""),
        "event_score": primary.get("event_score", primary.get("score", 0)),
        "event_evidence_refs": visible_evidence,
        "event_merge_reason": primary.get("event_merge_reason", ""),
        "matched_tokens": primary.get("matched_tokens", []),
        "matched_topics": primary.get("matched_topics", []),
        "time_window": primary.get("time_window", {}),
        "conflict_guard": primary.get("conflict_guard", {}),
        "event_summary": primary.get("event_summary", ""),
        "is_compact": item_limit is not None or evidence_limit is not None,
    }


def items_in_same_event(first: dict[str, Any], second: dict[str, Any]) -> bool:
    return bool(event_match_details(first, second)["matched"])


def event_match_details(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    if first.get("id") and first.get("id") == second.get("id"):
        return {
            "matched": True,
            "reason": "同一条情报",
            "matched_tokens": [],
            "matched_topics": [],
            "time_window": _event_time_details(first, second),
            "conflict_guard": _event_conflict_guard(first, second),
        }
    time_window = _event_time_details(first, second)
    conflict_guard = _event_conflict_guard(first, second)
    if not time_window["within_window"]:
        return {
            "matched": False,
            "reason": "发布时间不在归并窗口内",
            "matched_tokens": [],
            "matched_topics": [],
            "time_window": time_window,
            "conflict_guard": conflict_guard,
        }
    if conflict_guard["blocked"]:
        return {
            "matched": False,
            "reason": "命中相反动作词保护",
            "matched_tokens": [],
            "matched_topics": [],
            "time_window": time_window,
            "conflict_guard": conflict_guard,
        }
    first_title = _event_normalized_title(first)
    second_title = _event_normalized_title(second)
    if first_title and first_title == second_title:
        context = _event_context(first)
        return {
            "matched": True,
            "reason": "标题标准化后相同",
            "matched_tokens": sorted(context["tokens"])[:20],
            "matched_topics": sorted(context["topics"])[:20],
            "time_window": time_window,
            "conflict_guard": conflict_guard,
        }
    first_context = _event_context(first)
    second_context = _event_context(second)
    title_overlap = first_context["tokens"] & second_context["tokens"]
    topic_overlap = first_context["topics"] & second_context["topics"]
    token_floor = max(1, min(len(first_context["tokens"]), len(second_context["tokens"])))
    union_size = max(1, len(first_context["tokens"] | second_context["tokens"]))
    containment = len(title_overlap) / token_floor
    jaccard = len(title_overlap) / union_size
    matched = False
    reason = "未达到规则归并阈值"
    if topic_overlap and len(title_overlap) >= 4:
        matched = True
        reason = "标题 token 重叠且实体/标签重叠"
    elif len(topic_overlap) >= 2 and len(title_overlap) >= 2:
        matched = True
        reason = "多个实体/标签重叠且标题有交集"
    elif len(title_overlap) >= 5 and jaccard >= 0.42:
        matched = True
        reason = "标题相似度达到阈值"
    return {
        "matched": matched,
        "reason": reason,
        "matched_tokens": sorted(title_overlap)[:20],
        "matched_topics": sorted(topic_overlap)[:20],
        "containment": round(containment, 3),
        "jaccard": round(jaccard, 3),
        "time_window": time_window,
        "conflict_guard": conflict_guard,
    }


def apply_event_group(
    group: list[dict[str, Any]],
    insight_lookup: InsightLookup,
    primary: dict[str, Any] | None = None,
) -> None:
    if not group:
        return
    primary_item = primary or group[0]
    event_key = _event_key_for_group(group, primary_item)
    latest_item = max(group, key=_event_sort_value)
    sources = _event_sources(group)
    evidence_refs = _event_evidence_refs(group)
    explanation = _event_group_explanation(group)
    event_group = {
        "event_key": event_key,
        "primary_item_id": primary_item.get("id", ""),
        "title": primary_item.get("title", ""),
        "source_count": len(sources),
        "related_count": max(0, len(group) - 1),
        "latest_at": latest_item.get("published_at", ""),
        "merge_reason": explanation["merge_reason"],
        "matched_tokens": explanation["matched_tokens"],
        "matched_topics": explanation["matched_topics"],
        "time_window": explanation["time_window"],
        "conflict_guard": explanation["conflict_guard"],
    }
    for item in group:
        related = [_event_item_summary(candidate, insight_lookup) for candidate in group if candidate.get("id") != item.get("id")]
        item["event_key"] = event_key
        item["event_group"] = dict(event_group)
        item["related_count"] = len(related)
        item["related_items"] = related[:EVENT_RELATED_ITEM_LIMIT]
        item["source_count"] = len(sources)
        item["event_sources"] = sources
        item["event_latest_at"] = latest_item.get("published_at", "")
        item["event_score"] = max(float(candidate.get("score") or 0.0) for candidate in group)
        item["event_evidence_refs"] = evidence_refs[:EVENT_ITEM_DETAIL_LIMIT]
        item["event_merge_reason"] = explanation["merge_reason"]
        item["matched_tokens"] = explanation["matched_tokens"]
        item["matched_topics"] = explanation["matched_topics"]
        item["time_window"] = explanation["time_window"]
        item["conflict_guard"] = explanation["conflict_guard"]
        item["event_summary"] = _event_summary(group, sources, latest_item)


def _cached_insight_lookup(insight_lookup: InsightLookup) -> InsightLookup:
    cache: dict[str, dict[str, Any] | None] = {}

    def lookup(item_id: str) -> dict[str, Any] | None:
        if item_id not in cache:
            cache[item_id] = insight_lookup(item_id)
        return cache[item_id]

    return lookup


def _event_group_explanation(group: list[dict[str, Any]]) -> dict[str, Any]:
    if len(group) <= 1:
        return {
            "merge_reason": "单条情报",
            "matched_tokens": [],
            "matched_topics": [],
            "time_window": _event_group_time_window(group),
            "conflict_guard": {"applied": True, "blocked": False, "pairs": EVENT_CONFLICT_LABELS, "matched_conflicts": []},
        }
    matched_tokens: set[str] = set()
    matched_topics: set[str] = set()
    matched_conflicts: set[str] = set()
    reasons: list[str] = []
    for index, first in enumerate(group):
        for second in group[index + 1 :]:
            details = event_match_details(first, second)
            if details["matched"]:
                matched_tokens.update(details.get("matched_tokens", []))
                matched_topics.update(details.get("matched_topics", []))
                reasons.append(str(details.get("reason") or "规则归并"))
            guard = details.get("conflict_guard", {})
            matched_conflicts.update(guard.get("matched_conflicts", []) if isinstance(guard, dict) else [])
    return {
        "merge_reason": "；".join(list(dict.fromkeys(reasons))[:3]) or "标题相似、实体/标签重叠、发布时间相近",
        "matched_tokens": sorted(matched_tokens)[:20],
        "matched_topics": sorted(matched_topics)[:20],
        "time_window": _event_group_time_window(group),
        "conflict_guard": {
            "applied": True,
            "blocked": False,
            "pairs": EVENT_CONFLICT_LABELS,
            "matched_conflicts": sorted(matched_conflicts),
        },
    }


def _event_group_time_window(group: list[dict[str, Any]]) -> dict[str, Any]:
    times = [parsed for parsed in (_parse_event_time(str(item.get("published_at", ""))) for item in group) if parsed]
    span_seconds = 0
    if len(times) >= 2:
        span_seconds = int((max(times) - min(times)).total_seconds())
    return {
        "seconds": EVENT_TIME_WINDOW_SECONDS,
        "hours": int(EVENT_TIME_WINDOW_SECONDS / 3600),
        "actual_span_seconds": span_seconds,
        "within_window": span_seconds <= EVENT_TIME_WINDOW_SECONDS,
    }


def _event_context(item: dict[str, Any]) -> dict[str, set[str]]:
    title_tokens = _event_text_tokens(str(item.get("title", "")))
    topic_tokens: set[str] = set()
    for value in list(item.get("entities") or []) + list(item.get("tags") or []):
        normalized = _event_normalize_token(str(value))
        if normalized and normalized not in EVENT_STOPWORDS:
            topic_tokens.add(normalized)
    text_tokens = title_tokens | {token for token in topic_tokens if token not in EVENT_STOPWORDS}
    return {"tokens": text_tokens, "topics": topic_tokens}


def _event_text_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{1,}", text):
        token = _event_normalize_token(raw)
        if token and token not in EVENT_STOPWORDS:
            tokens.add(token)
    for sequence in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(sequence) <= 4:
            token = _event_normalize_token(sequence)
            if token and token not in EVENT_STOPWORDS:
                tokens.add(token)
        for size in (2, 3):
            for index in range(0, max(0, len(sequence) - size + 1)):
                token = _event_normalize_token(sequence[index : index + size])
                if token and token not in EVENT_STOPWORDS:
                    tokens.add(token)
    return tokens


def _event_normalize_token(value: str) -> str:
    token = re.sub(r"^[^\w\u4e00-\u9fff]+|[^\w\u4e00-\u9fff]+$", "", value.strip().lower())
    if len(token) < 2:
        return ""
    return token


def _event_normalized_title(item: dict[str, Any]) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(item.get("title", "")).lower())


def _event_conflict_guard(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    conflicts = _event_conflict_matches(first, second)
    return {
        "applied": True,
        "blocked": bool(conflicts),
        "pairs": EVENT_CONFLICT_LABELS,
        "matched_conflicts": conflicts,
    }


def _event_conflict_matches(first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    first_title = str(first.get("title", ""))
    second_title = str(second.get("title", ""))
    matches: list[str] = []
    for left, right in EVENT_CONFLICT_PAIRS:
        if (left in first_title and right in second_title) or (right in first_title and left in second_title):
            matches.append(f"{left}/{right}")
    return matches


def _event_time_details(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_time = _parse_event_time(str(first.get("published_at", "")))
    second_time = _parse_event_time(str(second.get("published_at", "")))
    if first_time and second_time:
        span_seconds = int(abs((first_time - second_time).total_seconds()))
        return {
            "seconds": EVENT_TIME_WINDOW_SECONDS,
            "hours": int(EVENT_TIME_WINDOW_SECONDS / 3600),
            "actual_span_seconds": span_seconds,
            "within_window": span_seconds <= EVENT_TIME_WINDOW_SECONDS,
        }
    first_day = str(first.get("published_at", ""))[:10]
    second_day = str(second.get("published_at", ""))[:10]
    return {
        "seconds": EVENT_TIME_WINDOW_SECONDS,
        "hours": int(EVENT_TIME_WINDOW_SECONDS / 3600),
        "actual_span_seconds": None,
        "within_window": bool(first_day and first_day == second_day),
    }


def _parse_event_time(value: str) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_sort_value(item: dict[str, Any]) -> tuple[float, str]:
    parsed = _parse_event_time(str(item.get("published_at", "")))
    timestamp = parsed.timestamp() if parsed else 0.0
    return (timestamp, str(item.get("created_at", "")))


def _event_key_for_group(group: list[dict[str, Any]], primary: dict[str, Any]) -> str:
    if len(group) == 1:
        return stable_hash("event", str(primary.get("id", "")))[:16]
    terms: set[str] = set()
    for item in group:
        context = _event_context(item)
        terms.update(context["topics"] or context["tokens"])
    if terms:
        return stable_hash("event", _event_bucket(primary), *sorted(terms)[:16])[:16]
    return stable_hash("event", *sorted(str(item.get("id", "")) for item in group))[:16]


def _event_bucket(item: dict[str, Any]) -> str:
    parsed = _parse_event_time(str(item.get("published_at", "")))
    if parsed:
        return parsed.date().isoformat()
    return str(item.get("published_at", ""))[:10] or "unknown-date"


def _event_sources(group: list[dict[str, Any]]) -> list[str]:
    sources: list[str] = []
    for item in group:
        source = str(item.get("source_name") or item.get("source_id") or "未知渠道")
        if source not in sources:
            sources.append(source)
    return sources


def _event_evidence_refs(group: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in group:
        url = str(item.get("source_url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        refs.append(
            {
                "kind": "source",
                "label": str(item.get("title") or item.get("source_name") or "原始来源"),
                "url": url,
                "source_name": str(item.get("source_name") or ""),
            }
        )
    return refs


def _event_item_summary(item: dict[str, Any], insight_lookup: InsightLookup) -> dict[str, Any]:
    translation = item.get("translation", {}) if isinstance(item.get("translation"), dict) else {}
    insight = insight_lookup(str(item.get("id", ""))) or {}
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "source_name": item.get("source_name", ""),
        "source_url": item.get("source_url", ""),
        "published_at": item.get("published_at", ""),
        "score": item.get("score", 0),
        "tags": item.get("tags", []),
        "risk_flags": insight.get("risk_flags", []),
        "translated_title": translation.get("translated_title", ""),
        "translated_summary": translation.get("translated_summary", ""),
        "translation_status": item.get("translation_status", ""),
        "summary": insight.get("summary", ""),
    }


def _event_summary(group: list[dict[str, Any]], sources: list[str], latest_item: dict[str, Any]) -> str:
    count = len(group)
    source_text = f"{len(sources)} 个来源" if sources else "未知来源"
    latest_at = str(latest_item.get("published_at") or "")
    if count <= 1:
        return f"单条情报，来自 {source_text}。"
    return f"{count} 条相关报道，来自 {source_text}，最近更新 {latest_at}。"
