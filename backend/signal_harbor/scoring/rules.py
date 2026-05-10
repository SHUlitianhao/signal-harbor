from __future__ import annotations

from signal_harbor.domain import Item


HIGH_VALUE_TERMS = ["业绩", "订单", "监管", "制裁", "供应", "涨价", "下调", "风险", "AI", "芯片"]


class RuleScorer:
    def score(self, item: Item, risk_flags: list[str]) -> tuple[float, list[str]]:
        score = 20.0
        signals: list[str] = []
        text = f"{item.title}\n{item.canonical_text}"
        for term in HIGH_VALUE_TERMS:
            if term in text:
                score += 7.0
                signals.append(f"命中关键词：{term}")
        if risk_flags:
            score += min(20.0, len(risk_flags) * 5.0)
            signals.append("包含风险提示词")
        if int(item.metadata.get("asset_count", 0)):
            score += 5.0
            signals.append("包含附件证据")
        if item.source_type in {"fixture", "rss", "json"}:
            score += 5.0
        return min(score, 100.0), signals[:8]
