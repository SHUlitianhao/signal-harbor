from __future__ import annotations

import re

from signal_harbor.domain import Extraction, Item


RISK_TERMS = [
    "风险",
    "下调",
    "制裁",
    "停产",
    "违约",
    "监管",
    "暴跌",
    "冲突",
    "risk",
    "risks",
    "downgrade",
    "sanction",
    "sanctions",
    "investigation",
    "regulation",
    "default",
    "conflict",
    "volatility",
]
ENTITY_PATTERN = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6}|[A-Z]{1,5}\.[A-Z]{1,3})(?![A-Za-z0-9])")


class RuleExtractor:
    def extract_entities(self, text: str) -> list[str]:
        entities = set(ENTITY_PATTERN.findall(text))
        for keyword in ["AI", "芯片", "新能源", "原油", "黄金", "美联储", "美元", "半导体"]:
            if keyword in text:
                entities.add(keyword)
        return sorted(entities)

    def extract_risk_flags(self, text: str) -> list[str]:
        lowered = text.lower()
        risks: list[str] = []
        for term in RISK_TERMS:
            if term.isascii():
                if re.search(rf"(?<![A-Za-z0-9]){re.escape(term.lower())}(?![A-Za-z0-9])", lowered):
                    risks.append(term)
            elif term in text:
                risks.append(term)
        return risks

    def extract(self, item: Item) -> list[Extraction]:
        entities = self.extract_entities(item.canonical_text)
        risks = self.extract_risk_flags(item.canonical_text)
        return [
            Extraction(
                item_id=item.id,
                kind="text",
                text=item.canonical_text,
                metadata={
                    "source_url": item.source_url,
                    "entities": entities,
                    "risk_flags": risks,
                },
            )
        ]
