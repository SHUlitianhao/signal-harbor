from __future__ import annotations

from signal_harbor.domain import Insight, Item, Notification


class InAppNotifier:
    def __init__(self, min_score: float = 70.0) -> None:
        self.min_score = min_score

    def maybe_create(self, item: Item, insight: Insight) -> Notification | None:
        high_risk = bool(insight.risk_flags and insight.score >= 60)
        if insight.score < self.min_score and not high_risk:
            return None
        risk = "，".join(insight.risk_flags) if insight.risk_flags else "高价值线索"
        return Notification(
            item_id=item.id,
            title=f"提醒：{item.title}",
            message=f"{risk}；评分 {insight.score:.0f}。{insight.summary}",
        )
