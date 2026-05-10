from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from signal_harbor.domain import Source


@dataclass
class RawContent:
    source_id: str
    source_type: str
    source_url: str
    title: str
    text: str
    published_at: str
    author: str | None = None
    tags: list[str] = field(default_factory=list)
    assets: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


class SourceAdapter(Protocol):
    source: Source

    def discover(self) -> list[str]:
        ...

    def fetch(self, discovered: list[str]) -> list[RawContent]:
        ...

    def parse(self, fetched: list[RawContent]) -> list[RawContent]:
        ...

    def normalize(self, raw: RawContent) -> RawContent:
        ...

    def collect(self) -> list[RawContent]:
        discovered = self.discover()
        fetched = self.fetch(discovered)
        return [self.normalize(item) for item in self.parse(fetched)]
