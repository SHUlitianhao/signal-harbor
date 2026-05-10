from __future__ import annotations


class RuleModelProvider:
    name = "rules-local"

    def summarize(self, text: str, limit: int = 150) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "..."
