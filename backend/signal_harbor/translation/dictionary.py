from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

from signal_harbor.config import PROJECT_ROOT
from signal_harbor.domain import Item
from signal_harbor.translation.language import infer_language, is_chinese_language


DEFAULT_TRANSLATION_CONFIG = PROJECT_ROOT / "config" / "translation.example.json"


class TranslationProvider(Protocol):
    name: str

    def translate(
        self,
        item: Item,
        summary: str,
        tags: list[str],
        risk_flags: list[str],
    ) -> dict[str, Any]:
        ...


class NullTranslationProvider:
    name = "disabled"

    def translate(
        self,
        item: Item,
        summary: str,
        tags: list[str],
        risk_flags: list[str],
    ) -> dict[str, Any]:
        return {}


class DictionaryTranslationProvider:
    name = "dictionary"

    def __init__(
        self,
        dictionary: dict[str, str],
        target_language: str = "zh",
        source_overrides: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        self.dictionary = {str(key): str(value) for key, value in dictionary.items() if str(key).strip()}
        self.target_language = target_language or "zh"
        self.source_overrides = source_overrides or {}
        self.enabled = enabled
        self._terms = sorted(self.dictionary, key=len, reverse=True)

    def translate(
        self,
        item: Item,
        summary: str,
        tags: list[str],
        risk_flags: list[str],
    ) -> dict[str, Any]:
        explicit_language = item.metadata.get("language") or item.metadata.get("lang") or item.lang
        source_language = infer_language(item.title, item.canonical_text, explicit_language)
        if not self.enabled:
            return {"status": "disabled", "provider": self.name, "source_language": source_language}
        if is_chinese_language(source_language) and is_chinese_language(self.target_language):
            return {
                "status": "not_required",
                "provider": self.name,
                "source_language": source_language,
                "target_language": self.target_language,
            }
        if not self._source_enabled(item):
            return {"status": "disabled", "provider": self.name, "source_language": source_language}

        translated_title, title_terms = self._translate_text(item.title)
        translated_summary, summary_terms = self._translate_text(summary)
        translated_tags, tag_terms = self._translate_values(tags)
        translated_risks, risk_terms = self._translate_values(risk_flags)
        matched_terms = sorted({*title_terms, *summary_terms, *tag_terms, *risk_terms}, key=str.lower)
        translated_terms = sorted({self.dictionary[term] for term in matched_terms if term in self.dictionary})
        untranslated_terms = self._untranslated_terms(item, summary, matched_terms)

        return {
            "status": "translated" if matched_terms else "missing_terms",
            "provider": self.name,
            "source_language": source_language,
            "target_language": self.target_language,
            "translated_title": translated_title if title_terms else "",
            "translated_summary": translated_summary if summary_terms else "",
            "translated_tags": translated_tags,
            "translated_risk_flags": translated_risks,
            "translated_terms": translated_terms,
            "matched_terms": matched_terms,
            "untranslated_terms": untranslated_terms,
        }

    def _source_enabled(self, item: Item) -> bool:
        override = self.source_overrides.get(item.source_id)
        if override is None:
            override = self.source_overrides.get(str(item.metadata.get("source_name", "")))
        if isinstance(override, dict) and "enabled" in override:
            return bool(override["enabled"])
        return True

    def _translate_text(self, text: str) -> tuple[str, list[str]]:
        translated = text
        matched: list[str] = []
        for term in self._terms:
            pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)
            if not pattern.search(translated):
                continue
            translated = pattern.sub(self.dictionary[term], translated)
            matched.append(term)
        return translated, matched

    def _translate_values(self, values: list[str]) -> tuple[list[str], list[str]]:
        translated: list[str] = []
        matched: list[str] = []
        for value in values:
            text, terms = self._translate_text(str(value))
            if terms and text not in translated:
                translated.append(text)
                matched.extend(terms)
        return translated, matched

    def _untranslated_terms(self, item: Item, summary: str, matched_terms: list[str]) -> list[str]:
        haystack = f"{item.title}\n{summary}\n{' '.join(item.tags)}"
        candidates = re.findall(r"\b[A-Z][A-Za-z]{2,}(?:\s+[A-Z][A-Za-z]{2,}){0,2}\b|\b[A-Z]{2,6}\b", haystack)
        matched_lower = {term.lower() for term in matched_terms}
        dictionary_lower = {term.lower() for term in self.dictionary}
        untranslated: list[str] = []
        for candidate in candidates:
            normalized = candidate.strip()
            lower = normalized.lower()
            if lower in matched_lower or lower in dictionary_lower:
                continue
            if normalized not in untranslated:
                untranslated.append(normalized)
        return untranslated[:12]


def load_translation_provider(
    path: str | Path | None = None,
    user_terms: list[dict[str, Any]] | None = None,
) -> TranslationProvider:
    config_path = _resolve_translation_path(path)
    if not config_path or not config_path.exists():
        return NullTranslationProvider()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    config = raw.get("translation", raw if isinstance(raw, dict) else {})
    if not isinstance(config, dict):
        return NullTranslationProvider()
    enabled = bool(config.get("enabled", False))
    provider = str(config.get("provider", "dictionary"))
    if not enabled or provider != "dictionary":
        return NullTranslationProvider()

    dictionary: dict[str, str] = {}
    dictionary.update(_string_dict(config.get("dictionary", {})))
    dictionary.update(_string_dict(config.get("tag_dictionary", {})))
    dictionary.update(_string_dict(config.get("risk_dictionary", {})))

    tag_dictionary_path = str(config.get("tag_dictionary_path", "") or "").strip()
    if tag_dictionary_path:
        extra_path = _project_path(tag_dictionary_path)
        if extra_path.exists() and extra_path.resolve() != config_path.resolve():
            extra_raw = json.loads(extra_path.read_text(encoding="utf-8"))
            dictionary.update(_string_dict(extra_raw.get("dictionary", extra_raw)))

    dictionary = _merge_user_terms(dictionary, user_terms or [])

    return DictionaryTranslationProvider(
        dictionary=dictionary,
        target_language=str(config.get("target_language", "zh")),
        source_overrides=dict(config.get("source_overrides", {})),
        enabled=enabled,
    )


def _resolve_translation_path(path: str | Path | None) -> Path | None:
    value = path or os.environ.get("SIGNAL_HARBOR_TRANSLATION_CONFIG")
    if value:
        return _project_path(value)
    return DEFAULT_TRANSLATION_CONFIG


def _project_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _string_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if str(key).strip() and str(item).strip()}


def _merge_user_terms(dictionary: dict[str, str], user_terms: list[dict[str, Any]]) -> dict[str, str]:
    merged = dict(dictionary)
    for term in user_terms:
        source_term = str(term.get("source_term", "") or "").strip()
        if not source_term:
            continue
        enabled = term.get("enabled", True)
        if not _truthy(enabled):
            merged.pop(source_term, None)
            continue
        target_term = str(term.get("target_term", "") or "").strip()
        if target_term:
            merged[source_term] = target_term
    return merged


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)
