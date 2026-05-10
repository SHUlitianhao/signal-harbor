from __future__ import annotations

import re


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{3,}\b")


def normalize_language(value: object) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    if raw.startswith(("zh", "cn", "chinese")):
        return "zh"
    if raw.startswith(("en", "english")):
        return "en"
    return raw.split("-", 1)[0]


def detect_language_from_text(*parts: str) -> str:
    sample = "\n".join(part for part in parts if part).strip()
    if not sample:
        return ""
    cjk_count = len(_CJK_RE.findall(sample))
    latin_words = _LATIN_WORD_RE.findall(sample)
    if cjk_count >= 2:
        return "zh"
    if len(latin_words) >= 2:
        return "en"
    if cjk_count:
        return "zh"
    if latin_words:
        return "en"
    return ""


def infer_language(title: str, text: str, explicit_language: object = "") -> str:
    explicit = normalize_language(explicit_language)
    detected = detect_language_from_text(title, text)
    if explicit == "zh" and detected == "en":
        return "en"
    if explicit:
        return explicit
    return detected or "zh"


def is_chinese_language(value: object) -> bool:
    return normalize_language(value).startswith("zh")


def is_english_language(value: object) -> bool:
    return normalize_language(value).startswith("en")
