from __future__ import annotations

from signal_harbor.adapters.base import RawContent, SourceAdapter
from typing import Any

from signal_harbor.domain import Asset, Extraction, Insight, Item, TaskRun, now_iso
from signal_harbor.extractors import RuleExtractor
from signal_harbor.model_providers import RuleModelProvider
from signal_harbor.notifiers import InAppNotifier
from signal_harbor.scoring import RuleScorer
from signal_harbor.storage import SQLiteStore
from signal_harbor.translation import load_translation_provider
from signal_harbor.translation.language import infer_language


class IngestPipeline:
    def __init__(
        self,
        store: SQLiteStore,
        extractor: RuleExtractor | None = None,
        model_provider: RuleModelProvider | None = None,
        scorer: RuleScorer | None = None,
        notifier: InAppNotifier | None = None,
        translation_provider: Any | None = None,
    ) -> None:
        self.store = store
        self.extractor = extractor or RuleExtractor()
        self.model_provider = model_provider or RuleModelProvider()
        self.scorer = scorer or RuleScorer()
        self.notifier = notifier or InAppNotifier()
        self.translation_provider = (
            translation_provider
            if translation_provider is not None
            else load_translation_provider(user_terms=store.list_glossary_terms())
        )

    def run_adapters(self, adapters: list[SourceAdapter]) -> list[TaskRun]:
        runs = []
        for adapter in adapters:
            runs.append(self.run_adapter(adapter))
        return runs

    def run_adapter(self, adapter: SourceAdapter) -> TaskRun:
        self.store.save_source(adapter.source)
        started_at = now_iso()
        try:
            raw_items = adapter.collect()
            filtered = int(getattr(adapter, "filtered_count", 0))
            created = 0
            for raw in raw_items:
                item = self._item_from_raw(raw)
                stored_item, is_new = self.store.upsert_item(item)
                if not is_new:
                    continue
                created += 1
                for asset in raw.assets:
                    self.store.add_asset(
                        Asset(
                            item_id=stored_item.id,
                            asset_type=asset.get("type", "link"),
                            url=asset.get("url", ""),
                            path=asset.get("path"),
                            metadata={key: value for key, value in asset.items() if key not in {"type", "url", "path"}},
                        )
                    )
                self._analyze(stored_item)
            task_run = TaskRun(
                source_id=adapter.source.id,
                task_type="ingest",
                status="success",
                started_at=started_at,
                finished_at=now_iso(),
                items_found=len(raw_items) + filtered,
                items_created=created,
                items_filtered=filtered,
                metadata=self._adapter_task_metadata(adapter),
            )
            self.store.add_task_run(task_run)
            self.store.update_source_status(adapter.source.id, success=True)
            return task_run
        except Exception as exc:
            task_run = TaskRun(
                source_id=adapter.source.id,
                task_type="ingest",
                status="failed",
                started_at=started_at,
                finished_at=now_iso(),
                items_filtered=int(getattr(adapter, "filtered_count", 0)),
                error=str(exc),
                metadata=self._adapter_task_metadata(adapter),
            )
            self.store.add_task_run(task_run)
            self.store.update_source_status(adapter.source.id, success=False, error=str(exc))
            return task_run

    def _item_from_raw(self, raw: RawContent) -> Item:
        text = "\n".join(line.strip() for line in raw.text.splitlines() if line.strip())
        metadata = dict(raw.metadata)
        metadata["asset_count"] = len(raw.assets)
        explicit_language = metadata.get("language") or metadata.get("lang")
        language = infer_language(raw.title, text, explicit_language)
        metadata["language"] = language
        if not explicit_language:
            metadata["detected_language"] = language
        return Item(
            source_id=raw.source_id,
            source_type=raw.source_type,
            source_url=raw.source_url,
            title=raw.title,
            canonical_text=text,
            published_at=raw.published_at,
            lang=language,
            author=raw.author,
            tags=raw.tags,
            metadata=metadata,
        )

    def _analyze(self, item: Item) -> None:
        extractions = self.extractor.extract(item)
        for extraction in extractions:
            self.store.add_extraction(extraction)

        primary = extractions[0]
        entities = list(primary.metadata.get("entities", []))
        risk_flags = list(primary.metadata.get("risk_flags", []))
        item.entities = entities
        item.tags = sorted(set(item.tags + entities + risk_flags))
        score, signals = self.scorer.score(item, risk_flags)
        summary = self.model_provider.summarize(item.canonical_text)
        translation = self._translate(item, summary, item.tags, risk_flags)
        if translation:
            item.metadata["translation"] = translation
            translated_tags = list(translation.get("translated_tags", []))
            translated_risks = list(translation.get("translated_risk_flags", []))
            translated_terms = list(translation.get("translated_terms", []))
            item.tags = sorted(set(item.tags + translated_tags + translated_risks + translated_terms))
            if translation.get("status") == "translated":
                self.store.add_extraction(
                    Extraction(
                        item_id=item.id,
                        kind="translation",
                        text=str(translation.get("translated_summary") or translation.get("translated_title") or ""),
                        metadata=translation,
                    )
                )
        evidence_refs = [{"kind": "source", "label": item.title, "url": item.source_url}]
        insight = Insight(
            item_id=item.id,
            summary=summary,
            signals=signals,
            risk_flags=risk_flags,
            evidence_refs=evidence_refs,
            model_used=self.model_provider.name,
            score=score,
        )
        self.store.add_insight(insight)
        self.store.update_item_analysis(item.id, item.entities, item.tags, score, metadata=item.metadata)

        notification = self.notifier.maybe_create(item, insight)
        if notification:
            self.store.add_notification(notification)

    def _translate(self, item: Item, summary: str, tags: list[str], risk_flags: list[str]) -> dict[str, Any]:
        try:
            result = self.translation_provider.translate(item, summary, tags, risk_flags)
            return result if isinstance(result, dict) else {}
        except Exception as exc:
            return {
                "status": "error",
                "provider": getattr(self.translation_provider, "name", "unknown"),
                "source_language": item.lang,
                "target_language": "zh",
                "error": str(exc),
            }

    def _adapter_task_metadata(self, adapter: SourceAdapter) -> dict[str, object]:
        metadata = getattr(adapter, "task_metadata", None)
        if callable(metadata):
            value = metadata()
            if isinstance(value, dict):
                return value
        return {}
