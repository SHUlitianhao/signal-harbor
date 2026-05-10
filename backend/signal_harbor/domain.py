from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def stable_hash(*parts: str | None) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").strip().encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def loads(value: str | None, default: Any) -> Any:
    if value in (None, ""):
        return default
    return json.loads(value)


@dataclass
class Source:
    name: str
    source_type: str
    location: str
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    fetch_interval_minutes: int = 60
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("src"))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_success_at: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Item:
    source_id: str
    source_type: str
    source_url: str
    title: str
    canonical_text: str
    published_at: str
    lang: str = "zh"
    author: str | None = None
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    score: float = 0.0
    status: str = "new"
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("item"))
    canonical_hash: str = ""
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def __post_init__(self) -> None:
        if not self.canonical_hash:
            self.canonical_hash = stable_hash(self.source_url, self.title, self.canonical_text)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Asset:
    item_id: str
    asset_type: str
    url: str
    path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("asset"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Extraction:
    item_id: str
    kind: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("ext"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Insight:
    item_id: str
    summary: str
    signals: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    related_assets: list[str] = field(default_factory=list)
    evidence_refs: list[dict[str, str]] = field(default_factory=list)
    model_used: str = "rules-local"
    score: float = 0.0
    id: str = field(default_factory=lambda: new_id("insight"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Watchlist:
    name: str
    keywords: list[str]
    enabled: bool = True
    id: str = field(default_factory=lambda: new_id("watch"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Collection:
    name: str
    description: str = ""
    item_ids: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: new_id("col"))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Favorite:
    item_id: str
    note: str = ""
    id: str = field(default_factory=lambda: new_id("fav"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SavedSearch:
    name: str
    query: dict[str, Any]
    id: str = field(default_factory=lambda: new_id("search"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AlertRule:
    name: str
    keywords: list[str]
    min_score: float = 60.0
    enabled: bool = True
    id: str = field(default_factory=lambda: new_id("alert"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskRun:
    source_id: str
    task_type: str
    status: str
    started_at: str
    finished_at: str | None = None
    items_found: int = 0
    items_created: int = 0
    items_filtered: int = 0
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("task"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Notification:
    title: str
    message: str
    item_id: str | None = None
    channel: str = "in_app"
    status: str = "unread"
    id: str = field(default_factory=lambda: new_id("msg"))
    created_at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
