from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from signal_harbor.adapters.base import RawContent
from signal_harbor.domain import Source, now_iso, stable_hash


DEFAULT_TIMEOUT_SECONDS = 15
VOID_HTML_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class _HtmlNode:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_HtmlNode"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    parent: "_HtmlNode | None" = None

    def text_content(self) -> str:
        parts: list[str] = []
        parts.extend(self.text_parts)
        for child in self.children:
            parts.append(child.text_content())
        return _clean_text(" ".join(parts))

    def descendants(self) -> list["_HtmlNode"]:
        nodes: list[_HtmlNode] = []
        for child in self.children:
            nodes.append(child)
            nodes.extend(child.descendants())
        return nodes


class _HtmlDocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _HtmlNode("__root__")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(
            tag=tag.lower(),
            attrs={name.lower(): value or "" for name, value in attrs},
            parent=self.current,
        )
        self.current.children.append(node)
        if node.tag not in VOID_HTML_TAGS:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _HtmlNode(
            tag=tag.lower(),
            attrs={name.lower(): value or "" for name, value in attrs},
            parent=self.current,
        )
        self.current.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        node = self.current
        while node.parent is not None:
            if node.tag == normalized:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.current.text_parts.append(data)


@dataclass
class PublicSourceConfig:
    name: str
    source_type: str
    url: str
    tags: list[str] = field(default_factory=list)
    enabled: bool = True
    fetch_interval_minutes: int = 60
    json_mapping: dict[str, str] = field(default_factory=dict)
    html_mapping: dict[str, Any] = field(default_factory=dict)
    rsshub_base_url: str = ""
    rsshub_route: str = ""
    rsshub_healthcheck_path: str = "/healthz"
    rsshub_check_health: bool = True
    rsshub_instance_name: str = ""
    description: str = ""
    publisher: str = ""
    region: str = ""
    market: str = ""
    language: str = ""
    quality_tier: str = ""
    include_keywords: list[str] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    default_topics: list[str] = field(default_factory=list)
    id: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PublicSourceConfig":
        source_type = str(value.get("type", value.get("source_type", ""))).lower()
        if source_type not in {"rss", "json", "html", "rsshub"}:
            raise ValueError(f"unsupported public source type: {source_type or '-'}")
        rsshub_base_url = str(value.get("rsshub_base_url", "")).strip()
        rsshub_route = str(value.get("rsshub_route", "")).strip()
        url = str(value.get("url") or "").strip()
        if source_type == "rsshub":
            url = url or _rsshub_url(rsshub_base_url, rsshub_route)
            if not url:
                raise ValueError("rsshub public source requires url or rsshub_base_url + rsshub_route")
        else:
            url = str(value["url"])
        return cls(
            id=value.get("id"),
            name=str(value["name"]),
            source_type=source_type,
            url=url,
            tags=[str(tag) for tag in value.get("tags", [])],
            enabled=_as_bool(value.get("enabled", True)),
            fetch_interval_minutes=int(value.get("fetch_interval_minutes", 60)),
            json_mapping={str(key): str(path) for key, path in dict(value.get("json_mapping", {})).items()},
            html_mapping=dict(value.get("html_mapping", {})),
            rsshub_base_url=rsshub_base_url,
            rsshub_route=rsshub_route,
            rsshub_healthcheck_path=str(value.get("rsshub_healthcheck_path", "/healthz")).strip() or "/healthz",
            rsshub_check_health=_as_bool(value.get("rsshub_check_health", True)),
            rsshub_instance_name=str(value.get("rsshub_instance_name", "")).strip(),
            description=str(value.get("description", "")),
            publisher=str(value.get("publisher", "")),
            region=str(value.get("region", "")),
            market=str(value.get("market", "")),
            language=str(value.get("language", "")),
            quality_tier=str(value.get("quality_tier", "")),
            include_keywords=_string_list(value.get("include_keywords", [])),
            exclude_keywords=_string_list(value.get("exclude_keywords", [])),
            default_topics=_string_list(value.get("default_topics", [])),
        )

    def to_source(self) -> Source:
        source_id = self.id or f"src_{stable_hash(self.source_type, self.url)[:16]}"
        return Source(
            id=source_id,
            name=self.name,
            source_type=self.source_type,
            location=self.url,
            tags=self.tags,
            enabled=self.enabled,
            fetch_interval_minutes=self.fetch_interval_minutes,
            metadata={
                "description": self.description,
                "publisher": self.publisher,
                "region": self.region,
                "market": self.market,
                "language": self.language,
                "quality_tier": self.quality_tier,
                "include_keywords": self.include_keywords,
                "exclude_keywords": self.exclude_keywords,
                "default_topics": self.default_topics,
                "html_mapping": self.html_mapping if self.source_type == "html" else {},
                "rsshub_base_url": self.rsshub_base_url,
                "rsshub_route": self.rsshub_route,
                "rsshub_url": self.url if self.source_type == "rsshub" else "",
                "rsshub_healthcheck_path": self.rsshub_healthcheck_path if self.source_type == "rsshub" else "",
                "rsshub_check_health": self.rsshub_check_health if self.source_type == "rsshub" else False,
                "rsshub_instance_name": self.rsshub_instance_name,
            },
        )

    def filter_summary(self) -> dict[str, Any]:
        return {
            "include_keywords": self.include_keywords,
            "exclude_keywords": self.exclude_keywords,
            "default_topics": self.default_topics,
            "rsshub_route": self.rsshub_route if self.source_type == "rsshub" else "",
            "rsshub_base_url": self.rsshub_base_url if self.source_type == "rsshub" else "",
        }


def load_public_source_configs(path: str) -> list[PublicSourceConfig]:
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, dict):
        values = raw.get("sources", [])
    else:
        values = []
    if not isinstance(values, list):
        raise ValueError("public sources config must be a list or contain a sources list")
    return [PublicSourceConfig.from_dict(item) for item in values]


def public_source_config_from_source(source: dict[str, Any]) -> PublicSourceConfig | None:
    source_type = str(source.get("source_type", "")).lower()
    if source_type not in {"rss", "rsshub", "json", "html"}:
        return None
    metadata = source.get("metadata", {}) if isinstance(source.get("metadata"), dict) else {}
    config: dict[str, Any] = {
        "id": source.get("id", ""),
        "name": source.get("name", ""),
        "type": source_type,
        "url": source.get("location", ""),
        "tags": source.get("tags", []),
        "enabled": bool(source.get("enabled", True)),
        "fetch_interval_minutes": int(source.get("fetch_interval_minutes", 60) or 60),
        "description": metadata.get("description", source.get("description", "")),
        "publisher": metadata.get("publisher", source.get("publisher", "")),
        "region": metadata.get("region", source.get("region", "")),
        "market": metadata.get("market", source.get("market", "")),
        "language": metadata.get("language", source.get("language", "")),
        "quality_tier": metadata.get("quality_tier", source.get("quality_tier", "")),
        "include_keywords": _string_list(metadata.get("include_keywords", source.get("include_keywords", []))),
        "exclude_keywords": _string_list(metadata.get("exclude_keywords", source.get("exclude_keywords", []))),
        "default_topics": _string_list(metadata.get("default_topics", source.get("default_topics", []))),
    }
    if source_type == "rss":
        if not str(config["url"]).strip():
            return None
    elif source_type == "rsshub":
        config.update(
            {
                "rsshub_base_url": str(metadata.get("rsshub_base_url", source.get("rsshub_base_url", ""))).strip(),
                "rsshub_route": str(metadata.get("rsshub_route", source.get("rsshub_route", ""))).strip(),
                "rsshub_healthcheck_path": str(
                    metadata.get("rsshub_healthcheck_path", source.get("rsshub_healthcheck_path", "/healthz"))
                ).strip()
                or "/healthz",
                "rsshub_check_health": _as_bool(metadata.get("rsshub_check_health", source.get("rsshub_check_health", True))),
                "rsshub_instance_name": str(
                    metadata.get("rsshub_instance_name", source.get("rsshub_instance_name", ""))
                ).strip(),
            }
        )
        if not str(config["url"]).strip() and not (config["rsshub_base_url"] and config["rsshub_route"]):
            return None
    elif source_type == "json":
        json_mapping = metadata.get("json_mapping", source.get("json_mapping", {}))
        if not isinstance(json_mapping, dict) or not json_mapping:
            return None
        config["json_mapping"] = json_mapping
    elif source_type == "html":
        html_mapping = metadata.get("html_mapping", source.get("html_mapping", {}))
        if not isinstance(html_mapping, dict) or not html_mapping:
            return None
        config["html_mapping"] = html_mapping
    try:
        return PublicSourceConfig.from_dict(config)
    except (KeyError, TypeError, ValueError):
        return None


def public_source_configs_from_sources(
    sources: list[dict[str, Any]],
    excluded_ids: set[str] | None = None,
) -> list[PublicSourceConfig]:
    excluded = excluded_ids or set()
    configs: list[PublicSourceConfig] = []
    for source in sources:
        source_id = str(source.get("id", ""))
        if source_id in excluded:
            continue
        config = public_source_config_from_source(source)
        if config:
            configs.append(config)
    return configs


def build_public_adapters(configs: list[PublicSourceConfig]) -> list["BasePublicSourceAdapter"]:
    adapters: list[BasePublicSourceAdapter] = []
    for config in configs:
        if not config.enabled:
            continue
        if config.source_type == "rss":
            adapters.append(RssSourceAdapter(config))
        elif config.source_type == "rsshub":
            adapters.append(RssHubSourceAdapter(config))
        elif config.source_type == "json":
            adapters.append(JsonSourceAdapter(config))
        elif config.source_type == "html":
            adapters.append(HtmlSourceAdapter(config))
    return adapters


class BasePublicSourceAdapter:
    def __init__(self, config: PublicSourceConfig) -> None:
        self.config = config
        self.source = config.to_source()
        self.filtered_count = 0
        self.filtered_reasons: list[dict[str, str]] = []

    def discover(self) -> list[str]:
        return [self.config.url]

    def parse(self, fetched: list[RawContent]) -> list[RawContent]:
        return fetched

    def normalize(self, raw: RawContent) -> RawContent:
        return RawContent(
            source_id=raw.source_id,
            source_type=raw.source_type,
            source_url=raw.source_url.strip() or self.config.url,
            title=" ".join(raw.title.split()) or self.config.name,
            text=_clean_text(raw.text),
            published_at=raw.published_at.strip() or now_iso(),
            author=raw.author.strip() if raw.author else None,
            tags=sorted({tag.strip() for tag in [*self.config.tags, *self.config.default_topics, *raw.tags] if tag.strip()}),
            assets=raw.assets,
            metadata={
                **raw.metadata,
                "source_name": self.config.name,
                "source_description": self.config.description,
                "publisher": self.config.publisher,
                "region": self.config.region,
                "market": self.config.market,
                "language": self.config.language,
                "quality_tier": self.config.quality_tier,
            },
        )

    def collect(self) -> list[RawContent]:
        self.filtered_count = 0
        self.filtered_reasons = []
        discovered = self.discover()
        fetched = self.fetch(discovered)
        normalized = [self.normalize(item) for item in self.parse(fetched)]
        accepted: list[RawContent] = []
        for item in normalized:
            reason = self._filter_reason(item)
            if reason:
                self.filtered_count += 1
                self.filtered_reasons.append({"title": item.title, "reason": reason})
                continue
            accepted.append(item)
        return accepted

    def task_metadata(self) -> dict[str, Any]:
        return {
            "filtered_reasons": self.filtered_reasons[:20],
            "filter_summary": self.config.filter_summary(),
        }

    def _filter_reason(self, raw: RawContent) -> str:
        haystack = f"{raw.title}\n{raw.text}\n{' '.join(raw.tags)}".lower()
        for keyword in self.config.exclude_keywords:
            if keyword.lower() in haystack:
                return f"excluded:{keyword}"
        if self.config.include_keywords and not any(keyword.lower() in haystack for keyword in self.config.include_keywords):
            return "missing_include_keyword"
        return ""

    def _read_text(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme in {"", "file"}:
            path = parsed.path if parsed.scheme == "file" else url
            with open(path, "r", encoding="utf-8") as handle:
                return handle.read()
        request = Request(url, headers={"User-Agent": "SignalHarbor/0.1"})
        with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")


class RssSourceAdapter(BasePublicSourceAdapter):
    def fetch(self, discovered: list[str]) -> list[RawContent]:
        if not discovered:
            return []
        xml_text = self._read_text(discovered[0])
        root = ElementTree.fromstring(xml_text)
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        return [self._raw_from_entry(entry) for entry in items]

    def _raw_from_entry(self, entry: ElementTree.Element) -> RawContent:
        title = _first_text(entry, ["title", "{http://www.w3.org/2005/Atom}title"])
        link = _rss_link(entry)
        description = _first_text(
            entry,
            [
                "description",
                "summary",
                "content",
                "{http://www.w3.org/2005/Atom}summary",
                "{http://www.w3.org/2005/Atom}content",
            ],
        )
        published_at = _parse_datetime(
            _first_text(entry, ["pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}updated"])
        )
        author = _first_text(entry, ["author", "dc:creator", "{http://www.w3.org/2005/Atom}author"])
        categories = _rss_categories(entry)
        return RawContent(
            source_id=self.source.id,
            source_type=self.source.source_type,
            source_url=link or self.config.url,
            title=title or self.config.name,
            text=description or title or "",
            published_at=published_at,
            author=author or None,
            tags=[*self.config.tags, *categories],
            metadata={"source_url": self.config.url, "adapter": "rss"},
        )


class RssHubSourceAdapter(RssSourceAdapter):
    def discover(self) -> list[str]:
        return [self.config.url]

    def fetch(self, discovered: list[str]) -> list[RawContent]:
        if not discovered:
            return []
        self._check_health()
        try:
            xml_text = self._read_text(discovered[0])
            root = ElementTree.fromstring(xml_text)
        except ElementTree.ParseError as exc:
            raise ValueError(f"RSSHub route returned non-RSS response: {exc}") from exc

        root_tag = _xml_local_name(root.tag)
        if root_tag not in {"rss", "feed", "rdf"}:
            raise ValueError(f"RSSHub route returned non-RSS response: root={root_tag or '-'}")
        items = root.findall(".//item")
        if not items:
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        if not items:
            raise ValueError("RSSHub route returned empty feed")

        raw_items = [self._raw_from_entry(entry) for entry in items]
        for item in raw_items:
            item.metadata.update(
                {
                    "adapter": "rsshub",
                    "rsshub_base_url": self.config.rsshub_base_url,
                    "rsshub_route": self.config.rsshub_route,
                    "rsshub_url": self.config.url,
                    "rsshub_instance_name": self.config.rsshub_instance_name,
                }
            )
        return raw_items

    def task_metadata(self) -> dict[str, Any]:
        metadata = super().task_metadata()
        metadata["rsshub"] = {
            "base_url": self.config.rsshub_base_url,
            "route": self.config.rsshub_route,
            "url": self.config.url,
            "instance_name": self.config.rsshub_instance_name,
            "check_health": self.config.rsshub_check_health,
            "healthcheck_path": self.config.rsshub_healthcheck_path,
        }
        return metadata

    def _check_health(self) -> None:
        if not self.config.rsshub_check_health or not self.config.rsshub_base_url:
            return
        health_url = _rsshub_url(self.config.rsshub_base_url, self.config.rsshub_healthcheck_path)
        body = self._read_text(health_url).strip()
        if body.lower() != "ok":
            preview = body[:120] or "<empty>"
            raise ValueError(f"RSSHub healthcheck failed: {health_url} returned {preview}")


class JsonSourceAdapter(BasePublicSourceAdapter):
    def fetch(self, discovered: list[str]) -> list[RawContent]:
        if not discovered:
            return []
        payload = json.loads(self._read_text(discovered[0]))
        items = _get_path(payload, self.config.json_mapping.get("items_path", ""))
        if items is None:
            items = payload
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            raise ValueError("JSON public source items_path must resolve to a list or object")
        return [self._raw_from_item(item) for item in items if isinstance(item, dict)]

    def _raw_from_item(self, item: dict[str, Any]) -> RawContent:
        mapping = self.config.json_mapping
        title = _as_text(_get_path(item, mapping.get("title", "title")))
        text = _as_text(_get_path(item, mapping.get("text", "text")))
        source_url = _as_text(_get_path(item, mapping.get("url", "url"))) or self.config.url
        published_at = _parse_datetime(_as_text(_get_path(item, mapping.get("published_at", "published_at"))))
        author = _as_text(_get_path(item, mapping.get("author", "author"))) or None
        raw_tags = _get_path(item, mapping.get("tags", "tags"))
        tags = [str(tag) for tag in raw_tags] if isinstance(raw_tags, list) else []
        return RawContent(
            source_id=self.source.id,
            source_type=self.source.source_type,
            source_url=source_url,
            title=title or self.config.name,
            text=text or title or "",
            published_at=published_at,
            author=author,
            tags=tags,
            metadata={"source_url": self.config.url, "adapter": "json"},
        )


class HtmlSourceAdapter(BasePublicSourceAdapter):
    def __init__(self, config: PublicSourceConfig) -> None:
        super().__init__(config)
        self.detail_errors: list[dict[str, str]] = []

    def fetch(self, discovered: list[str]) -> list[RawContent]:
        if not discovered:
            return []
        self.detail_errors = []
        list_url = discovered[0]
        document = _parse_html(self._read_text(list_url))
        item_selector = _html_selector(self.config.html_mapping, "items")
        if not item_selector:
            raise ValueError("html_mapping.items is required for html public sources")
        item_nodes = _select_nodes(document.root, item_selector)
        return [self._raw_from_item(node, list_url) for node in item_nodes]

    def task_metadata(self) -> dict[str, Any]:
        metadata = super().task_metadata()
        if self.detail_errors:
            metadata["detail_errors"] = self.detail_errors[:20]
        return metadata

    def _raw_from_item(self, node: _HtmlNode, list_url: str) -> RawContent:
        mapping = self.config.html_mapping
        title = _html_text(node, _html_selector(mapping, "title"))
        text = _html_text(node, _html_selector(mapping, "text"))
        source_url = self._extract_url(node, "url", list_url)
        detail_url = self._extract_url(node, "detail_url", list_url) or source_url
        metadata: dict[str, object] = {
            "source_url": self.config.url,
            "adapter": "html",
            "list_url": list_url,
        }
        if detail_url:
            metadata["detail_url"] = detail_url

        if _html_bool(mapping, "fetch_detail") and detail_url:
            try:
                detail_document = _parse_html(self._read_text(detail_url))
                detail_text = _html_text(detail_document.root, _html_selector(mapping, "detail_text"))
                if detail_text:
                    text = detail_text
                    metadata["detail_fetched"] = True
            except Exception as exc:
                error = {"url": detail_url, "error": str(exc)}
                self.detail_errors.append(error)
                metadata["detail_error"] = error

        published_at = _parse_datetime(_html_text(node, _html_selector(mapping, "published_at")))
        author = _html_text(node, _html_selector(mapping, "author")) or None
        tags = _html_texts(node, _html_selector(mapping, "tags"))
        return RawContent(
            source_id=self.source.id,
            source_type=self.source.source_type,
            source_url=source_url or detail_url or self.config.url,
            title=title or self.config.name,
            text=text or title or "",
            published_at=published_at,
            author=author,
            tags=tags,
            metadata=metadata,
        )

    def _extract_url(self, node: _HtmlNode, key: str, list_url: str) -> str:
        selector = _html_selector(self.config.html_mapping, key)
        if not selector:
            return ""
        attribute = _html_attribute(self.config.html_mapping, key, "href")
        selected = _select_first(node, selector)
        if not selected:
            return ""
        value = selected.attrs.get(attribute.lower(), "") if attribute else ""
        if not value:
            value = selected.attrs.get("href", "") or selected.text_content()
        return _resolve_html_url(value, _html_base_url(self.config.html_mapping, list_url))


def _parse_html(value: str) -> _HtmlDocumentParser:
    parser = _HtmlDocumentParser()
    parser.feed(value)
    parser.close()
    return parser


def _select_first(root: _HtmlNode, selector: str) -> _HtmlNode | None:
    nodes = _select_nodes(root, selector)
    return nodes[0] if nodes else None


def _select_nodes(root: _HtmlNode, selector: str) -> list[_HtmlNode]:
    parts = [part.strip() for part in selector.split() if part.strip()]
    if not parts:
        return []
    current = [root]
    for part in parts:
        next_nodes: list[_HtmlNode] = []
        for node in current:
            next_nodes.extend(descendant for descendant in node.descendants() if _matches_selector_part(descendant, part))
        current = next_nodes
    return current


def _matches_selector_part(node: _HtmlNode, selector: str) -> bool:
    token = selector.strip()
    if not token:
        return False
    attr_requirements = _selector_attr_requirements(token)
    token = re.sub(r"\[[^\]]+\]", "", token)
    tag = ""
    if token and token[0].isalpha():
        match = re.match(r"^([A-Za-z][\w:-]*)", token)
        if match:
            tag = match.group(1).lower()
            token = token[len(match.group(1)) :]
    elif token.startswith("*"):
        token = token[1:]
    node_id = ""
    classes: list[str] = []
    while token:
        if token.startswith("."):
            match = re.match(r"^\.([\w:-]+)", token)
            if not match:
                return False
            classes.append(match.group(1))
            token = token[len(match.group(0)) :]
        elif token.startswith("#"):
            match = re.match(r"^#([\w:-]+)", token)
            if not match:
                return False
            node_id = match.group(1)
            token = token[len(match.group(0)) :]
        else:
            return False
    if tag and node.tag != tag:
        return False
    if node_id and node.attrs.get("id", "") != node_id:
        return False
    node_classes = set(node.attrs.get("class", "").split())
    if any(required not in node_classes for required in classes):
        return False
    for name, expected in attr_requirements:
        actual = node.attrs.get(name)
        if actual is None:
            return False
        if expected is not None and actual != expected:
            return False
    return True


def _selector_attr_requirements(selector: str) -> list[tuple[str, str | None]]:
    requirements: list[tuple[str, str | None]] = []
    for raw in re.findall(r"\[([^\]]+)\]", selector):
        if "=" in raw:
            name, value = raw.split("=", 1)
            requirements.append((name.strip().lower(), value.strip().strip("\"'")))
        else:
            requirements.append((raw.strip().lower(), None))
    return requirements


def _html_selector(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key, "")
    if isinstance(value, dict):
        return str(value.get("selector", "") or value.get("path", "")).strip()
    if isinstance(value, str):
        return value.strip()
    return ""


def _html_attribute(mapping: dict[str, Any], key: str, default: str = "") -> str:
    value = mapping.get(key, "")
    if isinstance(value, dict):
        return str(value.get("attribute", default)).strip()
    return default


def _html_bool(mapping: dict[str, Any], key: str) -> bool:
    value = mapping.get(key, False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _html_base_url(mapping: dict[str, Any], list_url: str) -> str:
    value = mapping.get("base_url", "")
    return str(value).strip() or list_url


def _html_text(root: _HtmlNode, selector: str) -> str:
    node = _select_first(root, selector)
    return node.text_content() if node else ""


def _html_texts(root: _HtmlNode, selector: str) -> list[str]:
    return [text for text in (node.text_content() for node in _select_nodes(root, selector)) if text]


def _resolve_html_url(value: str, base_url: str) -> str:
    target = value.strip()
    if not target:
        return ""
    parsed_target = urlparse(target)
    if parsed_target.scheme:
        return target
    parsed_base = urlparse(base_url)
    if parsed_base.scheme in {"http", "https", "file"}:
        return urljoin(base_url, target)
    base_path = Path(base_url)
    if Path(target).is_absolute():
        return str(Path(target))
    return str(base_path.parent / target)


def _first_text(entry: ElementTree.Element, names: list[str]) -> str:
    for name in names:
        node = entry.find(name)
        if node is not None and node.text:
            return node.text
    return ""


def _rss_link(entry: ElementTree.Element) -> str:
    link = _first_text(entry, ["link", "{http://www.w3.org/2005/Atom}link"])
    if link:
        return link
    atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
    if atom_link is not None:
        return atom_link.attrib.get("href", "")
    guid = _first_text(entry, ["guid", "id", "{http://www.w3.org/2005/Atom}id"])
    return guid


def _rss_categories(entry: ElementTree.Element) -> list[str]:
    categories: list[str] = []
    for node in entry.findall("category") + entry.findall("{http://www.w3.org/2005/Atom}category"):
        text = node.text or node.attrib.get("term", "")
        if text and text.strip():
            categories.append(text.strip())
    return categories


def _xml_local_name(value: str) -> str:
    if "}" in value:
        return value.rsplit("}", 1)[-1].lower()
    return value.split(":")[-1].lower()


def _rsshub_url(base_url: str, route: str) -> str:
    route = str(route or "").strip()
    if urlparse(route).scheme:
        return _quote_url(route)
    base_url = str(base_url or "").strip()
    if not base_url:
        return _quote_url(route)
    if not route:
        return base_url
    return _quote_url(urljoin(base_url.rstrip("/") + "/", route.lstrip("/")))


def _quote_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme:
        return quote(url, safe="/%:?&=+,.#")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            quote(parsed.path, safe="/%"),
            quote(parsed.query, safe="=&%:+,/?"),
            quote(parsed.fragment, safe="%"),
        )
    )


def _parse_datetime(value: str) -> str:
    if not value:
        return now_iso()
    stripped = value.strip()
    try:
        return parsedate_to_datetime(stripped).isoformat()
    except (TypeError, ValueError, IndexError):
        return stripped


def _clean_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(without_tags).split())


def _get_path(value: Any, path: str) -> Any:
    if not path:
        return value
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False
