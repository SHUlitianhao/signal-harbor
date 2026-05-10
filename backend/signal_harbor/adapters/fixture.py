from __future__ import annotations

import json
from pathlib import Path

from signal_harbor.adapters.base import RawContent
from signal_harbor.domain import Source


class FixtureSourceAdapter:
    """Low-risk local adapter used for the first runnable chain and tests."""

    def __init__(self, fixture_path: str | Path, source: Source | None = None) -> None:
        self.fixture_path = Path(fixture_path)
        self.source = source or Source(
            id="src_fixture_local",
            name="本地 fixture 文本源",
            source_type="fixture",
            location=str(self.fixture_path),
            tags=["fixture", "公开文本"],
            fetch_interval_minutes=1440,
        )

    def discover(self) -> list[str]:
        return [str(self.fixture_path)]

    def fetch(self, discovered: list[str]) -> list[RawContent]:
        if not discovered:
            return []
        raw = json.loads(Path(discovered[0]).read_text(encoding="utf-8"))
        return [
            RawContent(
                source_id=self.source.id,
                source_type=self.source.source_type,
                source_url=item["source_url"],
                title=item["title"],
                text=item["text"],
                published_at=item["published_at"],
                author=item.get("author"),
                tags=list(item.get("tags", [])),
                assets=list(item.get("assets", [])),
                metadata=dict(item.get("metadata", {})),
            )
            for item in raw
        ]

    def parse(self, fetched: list[RawContent]) -> list[RawContent]:
        return fetched

    def normalize(self, raw: RawContent) -> RawContent:
        return RawContent(
            source_id=raw.source_id,
            source_type=raw.source_type,
            source_url=raw.source_url.strip(),
            title=" ".join(raw.title.split()),
            text="\n".join(line.strip() for line in raw.text.splitlines() if line.strip()),
            published_at=raw.published_at,
            author=raw.author.strip() if raw.author else None,
            tags=sorted({tag.strip() for tag in raw.tags if tag.strip()}),
            assets=raw.assets,
            metadata=raw.metadata,
        )

    def collect(self) -> list[RawContent]:
        discovered = self.discover()
        fetched = self.fetch(discovered)
        return [self.normalize(item) for item in self.parse(fetched)]
