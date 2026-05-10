from __future__ import annotations

from collections import Counter
import re
import sqlite3
from pathlib import Path
from typing import Any

from signal_harbor.domain import (
    AlertRule,
    Asset,
    Collection,
    Extraction,
    Favorite,
    Insight,
    Item,
    Notification,
    SavedSearch,
    Source,
    TaskRun,
    Watchlist,
    dumps,
    loads,
    new_id,
    now_iso,
)
from signal_harbor.events import (
    EVENT_CONTEXT_LIMIT,
    decorate_event_groups,
    decorate_single_item_event,
    event_group_payload,
    event_groups_for_items,
    items_in_same_event,
)
from signal_harbor.translation.language import infer_language, is_chinese_language, is_english_language


SQLITE_TIMEOUT_SECONDS = 30
SQLITE_BUSY_TIMEOUT_MS = SQLITE_TIMEOUT_SECONDS * 1000


class SQLiteStore:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.database_path,
            timeout=SQLITE_TIMEOUT_SECONDS,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal_mode()
        self.fts5_enabled = self._detect_fts5_support()
        self.initialize()

    def close(self) -> None:
        self.connection.close()

    def _enable_wal_mode(self) -> None:
        try:
            self.connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            # Some SQLite builds or special database paths may reject WAL; busy_timeout still applies.
            return

    def initialize(self) -> None:
        self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS sources (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              source_type TEXT NOT NULL,
              location TEXT NOT NULL,
              tags TEXT NOT NULL,
              enabled INTEGER NOT NULL,
              fetch_interval_minutes INTEGER NOT NULL,
              metadata TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_success_at TEXT,
              last_error TEXT
            );

            CREATE TABLE IF NOT EXISTS items (
              id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              source_type TEXT NOT NULL,
              source_url TEXT NOT NULL,
              title TEXT NOT NULL,
              canonical_text TEXT NOT NULL,
              lang TEXT NOT NULL,
              published_at TEXT NOT NULL,
              author TEXT,
              entities TEXT NOT NULL,
              tags TEXT NOT NULL,
              score REAL NOT NULL,
              status TEXT NOT NULL,
              canonical_hash TEXT NOT NULL UNIQUE,
              metadata TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              FOREIGN KEY(source_id) REFERENCES sources(id)
            );

            CREATE TABLE IF NOT EXISTS assets (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              asset_type TEXT NOT NULL,
              url TEXT NOT NULL,
              path TEXT,
              metadata TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS extractions (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              text TEXT NOT NULL,
              metadata TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS insights (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL,
              summary TEXT NOT NULL,
              signals TEXT NOT NULL,
              risk_flags TEXT NOT NULL,
              related_assets TEXT NOT NULL,
              evidence_refs TEXT NOT NULL,
              model_used TEXT NOT NULL,
              score REAL NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS watchlists (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              keywords TEXT NOT NULL,
              enabled INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collections (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              description TEXT NOT NULL,
              item_ids TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS favorites (
              id TEXT PRIMARY KEY,
              item_id TEXT NOT NULL UNIQUE,
              note TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS saved_searches (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              query TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
              id TEXT PRIMARY KEY,
              name TEXT NOT NULL,
              keywords TEXT NOT NULL,
              min_score REAL NOT NULL,
              enabled INTEGER NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notifications (
              id TEXT PRIMARY KEY,
              item_id TEXT,
              channel TEXT NOT NULL,
              title TEXT NOT NULL,
              message TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(item_id) REFERENCES items(id)
            );

            CREATE TABLE IF NOT EXISTS task_runs (
              id TEXT PRIMARY KEY,
              source_id TEXT NOT NULL,
              task_type TEXT NOT NULL,
              status TEXT NOT NULL,
              started_at TEXT NOT NULL,
              finished_at TEXT,
              items_found INTEGER NOT NULL,
              items_created INTEGER NOT NULL,
              items_filtered INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              metadata TEXT NOT NULL DEFAULT '{}',
              FOREIGN KEY(source_id) REFERENCES sources(id)
            );

            CREATE TABLE IF NOT EXISTS glossary_terms (
              id TEXT PRIMARY KEY,
              source_term TEXT NOT NULL,
              target_term TEXT NOT NULL,
              category TEXT NOT NULL,
              enabled INTEGER NOT NULL,
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE(source_term, category)
            );
            """
        )
        self._migrate_schema()
        self._initialize_search_index()
        self.connection.commit()

    def _migrate_schema(self) -> None:
        self._ensure_column("sources", "metadata", "TEXT NOT NULL DEFAULT '{}'")
        self._ensure_column("task_runs", "items_filtered", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("task_runs", "metadata", "TEXT NOT NULL DEFAULT '{}'")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _detect_fts5_support(self) -> bool:
        options = [row[0] for row in self.connection.execute("PRAGMA compile_options").fetchall()]
        if any(option == "ENABLE_FTS5" for option in options):
            return True
        try:
            self.connection.execute("CREATE VIRTUAL TABLE temp._fts5_probe USING fts5(value)")
            self.connection.execute("DROP TABLE temp._fts5_probe")
            return True
        except sqlite3.DatabaseError:
            return False

    def _initialize_search_index(self) -> None:
        if not self.fts5_enabled:
            return
        try:
            self.connection.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS item_search USING fts5(
                  item_id UNINDEXED,
                  title,
                  canonical_text,
                  entities,
                  tags,
                  source_url,
                  author
                )
                """
            )
            self.rebuild_search_index()
        except sqlite3.DatabaseError:
            self.fts5_enabled = False

    def rebuild_search_index(self) -> None:
        if not self.fts5_enabled:
            return
        self.connection.execute("DELETE FROM item_search")
        rows = self.connection.execute("SELECT * FROM items").fetchall()
        for row in rows:
            self._index_item_row(row)
        self.connection.commit()

    def save_source(self, source: Source) -> Source:
        source.updated_at = now_iso()
        self.connection.execute(
            """
            INSERT INTO sources (
              id, name, source_type, location, tags, enabled, fetch_interval_minutes,
              metadata, created_at, updated_at, last_success_at, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,
              source_type=excluded.source_type,
              location=excluded.location,
              tags=excluded.tags,
              enabled=excluded.enabled,
              fetch_interval_minutes=excluded.fetch_interval_minutes,
              metadata=excluded.metadata,
              updated_at=excluded.updated_at,
              last_success_at=excluded.last_success_at,
              last_error=excluded.last_error
            """,
            (
                source.id,
                source.name,
                source.source_type,
                source.location,
                dumps(source.tags),
                int(source.enabled),
                source.fetch_interval_minutes,
                dumps(source.metadata),
                source.created_at,
                source.updated_at,
                source.last_success_at,
                source.last_error,
            ),
        )
        self.connection.commit()
        return source

    def list_sources(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM sources WHERE source_type != ? ORDER BY name", ("runtime",))
        return [self._source_row(row) for row in rows]

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source_row(row) if row else None

    def update_source_status(self, source_id: str, success: bool, error: str | None = None) -> None:
        if success:
            self.connection.execute(
                "UPDATE sources SET last_success_at = ?, last_error = NULL, updated_at = ? WHERE id = ?",
                (now_iso(), now_iso(), source_id),
            )
        else:
            self.connection.execute(
                "UPDATE sources SET last_error = ?, updated_at = ? WHERE id = ?",
                (error, now_iso(), source_id),
            )
        self.connection.commit()

    def set_source_enabled(self, source_id: str, enabled: bool) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            return None
        self.connection.execute(
            "UPDATE sources SET enabled = ?, updated_at = ? WHERE id = ?",
            (int(enabled), now_iso(), source_id),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return self._source_row(row)

    def upsert_item(self, item: Item) -> tuple[Item, bool]:
        existing = self.connection.execute(
            "SELECT * FROM items WHERE canonical_hash = ?", (item.canonical_hash,)
        ).fetchone()
        if existing:
            return self._item_from_row(existing), False

        self.connection.execute(
            """
            INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.source_id,
                item.source_type,
                item.source_url,
                item.title,
                item.canonical_text,
                item.lang,
                item.published_at,
                item.author,
                dumps(item.entities),
                dumps(item.tags),
                item.score,
                item.status,
                item.canonical_hash,
                dumps(item.metadata),
                item.created_at,
                item.updated_at,
            ),
        )
        if self.fts5_enabled:
            self._index_item(item)
        self.connection.commit()
        return item, True

    def update_item_analysis(
        self,
        item_id: str,
        entities: list[str],
        tags: list[str],
        score: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if metadata is None:
            self.connection.execute(
                "UPDATE items SET entities = ?, tags = ?, score = ?, status = ?, updated_at = ? WHERE id = ?",
                (dumps(entities), dumps(tags), score, "processed", now_iso(), item_id),
            )
        else:
            self.connection.execute(
                """
                UPDATE items
                SET entities = ?, tags = ?, score = ?, status = ?, metadata = ?, updated_at = ?
                WHERE id = ?
                """,
                (dumps(entities), dumps(tags), score, "processed", dumps(metadata), now_iso(), item_id),
            )
        if self.fts5_enabled:
            row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if row:
                self._index_item_row(row)
        self.connection.commit()

    def add_asset(self, asset: Asset) -> Asset:
        self.connection.execute(
            "INSERT INTO assets VALUES (?, ?, ?, ?, ?, ?)",
            (asset.id, asset.item_id, asset.asset_type, asset.url, asset.path, dumps(asset.metadata)),
        )
        self.connection.commit()
        return asset

    def add_extraction(self, extraction: Extraction) -> Extraction:
        self.connection.execute(
            "INSERT INTO extractions VALUES (?, ?, ?, ?, ?, ?)",
            (
                extraction.id,
                extraction.item_id,
                extraction.kind,
                extraction.text,
                dumps(extraction.metadata),
                extraction.created_at,
            ),
        )
        self.connection.commit()
        return extraction

    def apply_item_translation(self, item_id: str, translation: dict[str, Any]) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        item = self._item_row(row)
        metadata = dict(item["metadata"])
        previous = metadata.get("translation", {}) if isinstance(metadata.get("translation"), dict) else {}
        previous_terms = set(
            list(previous.get("translated_tags", []))
            + list(previous.get("translated_risk_flags", []))
            + list(previous.get("translated_terms", []))
        )
        metadata["translation"] = translation
        tags = sorted(
            set(
                [tag for tag in item["tags"] if tag not in previous_terms]
                + list(translation.get("translated_tags", []))
                + list(translation.get("translated_risk_flags", []))
                + list(translation.get("translated_terms", []))
            )
        )
        self.connection.execute(
            """
            UPDATE items
            SET tags = ?, metadata = ?, updated_at = ?
            WHERE id = ?
            """,
            (dumps(tags), dumps(metadata), now_iso(), item_id),
        )
        if translation.get("status") == "translated":
            self.add_extraction(
                Extraction(
                    item_id=item_id,
                    kind="translation",
                    text=str(translation.get("translated_summary") or translation.get("translated_title") or ""),
                    metadata={**translation, "trigger": "manual"},
                )
            )
        if self.fts5_enabled:
            row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if row:
                self._index_item_row(row)
        self.connection.commit()
        return self.get_item(item_id)

    def list_glossary_terms(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        if enabled_only:
            rows = self.connection.execute(
                "SELECT * FROM glossary_terms WHERE enabled = 1 ORDER BY lower(source_term)"
            ).fetchall()
        else:
            rows = self.connection.execute("SELECT * FROM glossary_terms ORDER BY lower(source_term)").fetchall()
        return [self._glossary_row(row) for row in rows]

    def create_glossary_term(
        self,
        source_term: str,
        target_term: str,
        category: str = "dictionary",
        enabled: bool = True,
        notes: str = "",
    ) -> dict[str, Any]:
        term_id = new_id("term")
        now = now_iso()
        self.connection.execute(
            """
            INSERT INTO glossary_terms (
              id, source_term, target_term, category, enabled, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_term, category) DO UPDATE SET
              target_term=excluded.target_term,
              enabled=excluded.enabled,
              notes=excluded.notes,
              updated_at=excluded.updated_at
            """,
            (
                term_id,
                source_term.strip(),
                target_term.strip(),
                category.strip() or "dictionary",
                int(enabled),
                notes.strip(),
                now,
                now,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT * FROM glossary_terms WHERE source_term = ? AND category = ?",
            (source_term.strip(), category.strip() or "dictionary"),
        ).fetchone()
        return self._glossary_row(row)

    def update_glossary_term(self, term_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM glossary_terms WHERE id = ?", (term_id,)).fetchone()
        if not row:
            return None
        current = self._glossary_row(row)
        source_term = str(updates.get("source_term", current["source_term"])).strip()
        target_term = str(updates.get("target_term", current["target_term"])).strip()
        category = str(updates.get("category", current["category"])).strip() or "dictionary"
        enabled = self._coerce_bool(updates.get("enabled", current["enabled"]))
        notes = str(updates.get("notes", current["notes"])).strip()
        self.connection.execute(
            """
            UPDATE glossary_terms
            SET source_term = ?, target_term = ?, category = ?, enabled = ?, notes = ?, updated_at = ?
            WHERE id = ?
            """,
            (source_term, target_term, category, int(enabled), notes, now_iso(), term_id),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM glossary_terms WHERE id = ?", (term_id,)).fetchone()
        return self._glossary_row(row)

    def delete_glossary_term(self, term_id: str) -> bool:
        cursor = self.connection.execute("DELETE FROM glossary_terms WHERE id = ?", (term_id,))
        self.connection.commit()
        return cursor.rowcount > 0

    def add_insight(self, insight: Insight) -> Insight:
        self.connection.execute(
            "INSERT INTO insights VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                insight.id,
                insight.item_id,
                insight.summary,
                dumps(insight.signals),
                dumps(insight.risk_flags),
                dumps(insight.related_assets),
                dumps(insight.evidence_refs),
                insight.model_used,
                insight.score,
                insight.created_at,
            ),
        )
        self.connection.commit()
        return insight

    def add_notification(self, notification: Notification) -> Notification:
        if notification.item_id and self._has_recent_event_notification(notification.item_id):
            return notification
        self.connection.execute(
            "INSERT INTO notifications VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                notification.id,
                notification.item_id,
                notification.channel,
                notification.title,
                notification.message,
                notification.status,
                notification.created_at,
            ),
        )
        self.connection.commit()
        return notification

    def add_favorite(self, favorite: Favorite) -> Favorite:
        self.connection.execute(
            """
            INSERT INTO favorites VALUES (?, ?, ?, ?)
            ON CONFLICT(item_id) DO UPDATE SET note=excluded.note
            """,
            (favorite.id, favorite.item_id, favorite.note, favorite.created_at),
        )
        self.connection.commit()
        return favorite

    def create_collection(self, collection: Collection) -> Collection:
        self.connection.execute(
            "INSERT INTO collections VALUES (?, ?, ?, ?, ?, ?)",
            (
                collection.id,
                collection.name,
                collection.description,
                dumps(collection.item_ids),
                collection.created_at,
                collection.updated_at,
            ),
        )
        self.connection.commit()
        return collection

    def append_item_to_collection(self, collection_id: str, item_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if not row:
            return None
        item_ids = loads(row["item_ids"], [])
        if item_id not in item_ids:
            item_ids.append(item_id)
            self.connection.execute(
                "UPDATE collections SET item_ids = ?, updated_at = ? WHERE id = ?",
                (dumps(item_ids), now_iso(), collection_id),
            )
            self.connection.commit()
            row = self.connection.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        return self._collection_row(row)

    def create_watchlist(self, watchlist: Watchlist) -> Watchlist:
        self.connection.execute(
            "INSERT INTO watchlists VALUES (?, ?, ?, ?, ?)",
            (watchlist.id, watchlist.name, dumps(watchlist.keywords), int(watchlist.enabled), watchlist.created_at),
        )
        self.connection.commit()
        return watchlist

    def create_saved_search(self, saved_search: SavedSearch) -> SavedSearch:
        self.connection.execute(
            "INSERT INTO saved_searches VALUES (?, ?, ?, ?)",
            (saved_search.id, saved_search.name, dumps(saved_search.query), saved_search.created_at),
        )
        self.connection.commit()
        return saved_search

    def create_alert_rule(self, alert_rule: AlertRule) -> AlertRule:
        self.connection.execute(
            "INSERT INTO alert_rules VALUES (?, ?, ?, ?, ?, ?)",
            (
                alert_rule.id,
                alert_rule.name,
                dumps(alert_rule.keywords),
                alert_rule.min_score,
                int(alert_rule.enabled),
                alert_rule.created_at,
            ),
        )
        self.connection.commit()
        return alert_rule

    def set_alert_rule_enabled(self, alert_rule_id: str, enabled: bool) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_rule_id,)).fetchone()
        if not row:
            return None
        self.connection.execute(
            "UPDATE alert_rules SET enabled = ? WHERE id = ?",
            (int(enabled), alert_rule_id),
        )
        self.connection.commit()
        row = self.connection.execute("SELECT * FROM alert_rules WHERE id = ?", (alert_rule_id,)).fetchone()
        return self._alert_rule_row(row)

    def add_task_run(self, task_run: TaskRun) -> TaskRun:
        self.connection.execute(
            """
            INSERT INTO task_runs (
              id, source_id, task_type, status, started_at, finished_at,
              items_found, items_created, items_filtered, error, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_run.id,
                task_run.source_id,
                task_run.task_type,
                task_run.status,
                task_run.started_at,
                task_run.finished_at,
                task_run.items_found,
                task_run.items_created,
                task_run.items_filtered,
                task_run.error,
                dumps(task_run.metadata),
            ),
        )
        self.connection.commit()
        return task_run

    def list_latest_items(
        self,
        limit: int = 50,
        source_id: str | None = None,
        translation_status: str | None = None,
    ) -> list[dict[str, Any]]:
        clean_limit = self._clean_limit(limit)
        sql_limit = EVENT_CONTEXT_LIMIT if translation_status else min(EVENT_CONTEXT_LIMIT, max(clean_limit * 4, clean_limit))
        if source_id:
            rows = self.connection.execute(
                "SELECT * FROM items WHERE source_id = ? ORDER BY published_at DESC, created_at DESC LIMIT ?",
                (source_id, sql_limit),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT i.*
                FROM items i
                JOIN sources s ON s.id = i.source_id
                WHERE s.enabled = 1
                ORDER BY i.published_at DESC, i.created_at DESC
                LIMIT ?
                """,
                (sql_limit,),
            ).fetchall()
        items = [self._item_row(row) for row in rows]
        filtered = self._filter_items_by_translation_status(items, translation_status, EVENT_CONTEXT_LIMIT)
        return self._decorate_event_groups(filtered, collapse=True)[:clean_limit]

    def search_items(
        self,
        query: str = "",
        source_id: str | None = None,
        tag: str | None = None,
        topic: str | None = None,
        min_score: float | None = None,
        favorite: bool | None = None,
        published_from: str | None = None,
        published_to: str | None = None,
        translation_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if query and self.fts5_enabled:
            try:
                return self._search_items_fts(
                    query=query,
                    source_id=source_id,
                    tag=tag,
                    topic=topic,
                    min_score=min_score,
                    favorite=favorite,
                    published_from=published_from,
                    published_to=published_to,
                    translation_status=translation_status,
                    limit=limit,
                )
            except sqlite3.DatabaseError:
                return self._search_items_like(
                    query=query,
                    source_id=source_id,
                    tag=tag,
                    topic=topic,
                    min_score=min_score,
                    favorite=favorite,
                    published_from=published_from,
                    published_to=published_to,
                    translation_status=translation_status,
                    limit=limit,
                )
        return self._search_items_like(
            query=query,
            source_id=source_id,
            tag=tag,
            topic=topic,
            min_score=min_score,
            favorite=favorite,
            published_from=published_from,
            published_to=published_to,
            translation_status=translation_status,
            limit=limit,
        )

    def _search_items_like(
        self,
        query: str = "",
        source_id: str | None = None,
        tag: str | None = None,
        topic: str | None = None,
        min_score: float | None = None,
        favorite: bool | None = None,
        published_from: str | None = None,
        published_to: str | None = None,
        translation_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clean_limit = self._clean_limit(limit)
        sql_limit = EVENT_CONTEXT_LIMIT if translation_status else min(EVENT_CONTEXT_LIMIT, max(clean_limit * 4, clean_limit))
        sql = ["SELECT DISTINCT i.* FROM items i LEFT JOIN favorites f ON f.item_id = i.id WHERE 1=1"]
        params: list[Any] = []
        if query:
            sql.append(
                "AND (i.title LIKE ? OR i.canonical_text LIKE ? OR i.entities LIKE ? OR i.tags LIKE ? OR i.source_url LIKE ?)"
            )
            like = f"%{query}%"
            params.extend([like, like, like, like, like])
        self._append_item_filters(sql, params, source_id, tag, topic, min_score, favorite, published_from, published_to)
        sql.append("ORDER BY i.published_at DESC, i.created_at DESC LIMIT ?")
        params.append(sql_limit)
        items = [self._item_row(row) for row in self.connection.execute(" ".join(sql), params).fetchall()]
        filtered = self._filter_items_by_translation_status(items, translation_status, EVENT_CONTEXT_LIMIT)
        return self._decorate_event_groups(filtered, collapse=True)[:clean_limit]

    def _search_items_fts(
        self,
        query: str,
        source_id: str | None = None,
        tag: str | None = None,
        topic: str | None = None,
        min_score: float | None = None,
        favorite: bool | None = None,
        published_from: str | None = None,
        published_to: str | None = None,
        translation_status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        match_query = self._fts_match_query(query)
        if not match_query:
            return self._search_items_like(
                query=query,
                source_id=source_id,
                tag=tag,
                topic=topic,
                min_score=min_score,
                favorite=favorite,
                published_from=published_from,
                published_to=published_to,
                translation_status=translation_status,
                limit=limit,
            )
        clean_limit = self._clean_limit(limit)
        sql_limit = EVENT_CONTEXT_LIMIT if translation_status else min(EVENT_CONTEXT_LIMIT, max(clean_limit * 4, clean_limit))
        sql = [
            """
            SELECT DISTINCT i.*, s.rank AS search_rank
            FROM item_search s
            JOIN items i ON i.id = s.item_id
            LEFT JOIN favorites f ON f.item_id = i.id
            WHERE item_search MATCH ?
            """
        ]
        params: list[Any] = [match_query]
        self._append_item_filters(sql, params, source_id, tag, topic, min_score, favorite, published_from, published_to)
        sql.append("ORDER BY s.rank ASC, i.published_at DESC, i.created_at DESC LIMIT ?")
        params.append(sql_limit)
        items = [self._item_row(row) for row in self.connection.execute(" ".join(sql), params).fetchall()]
        filtered = self._filter_items_by_translation_status(items, translation_status, EVENT_CONTEXT_LIMIT)
        return self._decorate_event_groups(filtered, collapse=True)[:clean_limit]

    def _append_item_filters(
        self,
        sql: list[str],
        params: list[Any],
        source_id: str | None,
        tag: str | None,
        topic: str | None,
        min_score: float | None,
        favorite: bool | None,
        published_from: str | None,
        published_to: str | None,
    ) -> None:
        if source_id:
            sql.append("AND i.source_id = ?")
            params.append(source_id)
        topic_or_tag = topic or tag
        if topic_or_tag:
            sql.append("AND (i.tags LIKE ? OR i.entities LIKE ?)")
            like = f"%{topic_or_tag}%"
            params.extend([like, like])
        if min_score is not None:
            sql.append("AND i.score >= ?")
            params.append(min_score)
        if favorite is True:
            sql.append("AND f.id IS NOT NULL")
        elif favorite is False:
            sql.append("AND f.id IS NULL")
        if published_from:
            sql.append("AND i.published_at >= ?")
            params.append(published_from)
        if published_to:
            sql.append("AND i.published_at <= ?")
            params.append(published_to)

    def _clean_limit(self, limit: int) -> int:
        return max(1, min(int(limit), 500))

    def _fts_match_query(self, query: str) -> str:
        terms = re.findall(r"[\w\u4e00-\u9fff]+", query, re.UNICODE)
        return " ".join(f'"{term}"*' for term in terms if term)

    def list_items_for_translation(
        self,
        source_id: str | None = None,
        translation_status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clean_limit = self._clean_limit(limit)
        params: list[Any] = []
        sql = ["SELECT * FROM items WHERE 1=1"]
        if source_id:
            sql.append("AND source_id = ?")
            params.append(source_id)
        sql.append("ORDER BY published_at DESC, created_at DESC LIMIT ?")
        params.append(500 if translation_status else clean_limit)
        items = [self._item_row(row) for row in self.connection.execute(" ".join(sql), params).fetchall()]
        return self._filter_items_by_translation_status(items, translation_status, clean_limit)

    def translation_status_summary(self) -> dict[str, Any]:
        rows = self.connection.execute("SELECT * FROM items ORDER BY published_at DESC, created_at DESC").fetchall()
        items = [self._item_row(row) for row in rows]
        counter: Counter[str] = Counter()
        untranslated_terms: Counter[str] = Counter()
        english_items = 0
        for item in items:
            status = self._translation_status_for_item(item)
            counter[status] += 1
            source_language = self._item_source_language(item, item.get("translation", {}))
            if is_english_language(source_language):
                english_items += 1
            translation = item.get("translation", {})
            for term in translation.get("untranslated_terms", []) if isinstance(translation, dict) else []:
                normalized = str(term).strip()
                if normalized:
                    untranslated_terms[normalized] += 1
        translated = counter.get("translated", 0)
        coverage = round((translated / english_items) * 100, 1) if english_items else 0.0
        return {
            "total_items": len(items),
            "english_items": english_items,
            "translated": translated,
            "missing_terms": counter.get("missing_terms", 0),
            "error": counter.get("error", 0),
            "disabled": counter.get("disabled", 0),
            "not_required": counter.get("not_required", 0),
            "untranslated": counter.get("untranslated", 0),
            "coverage": coverage,
            "high_frequency_untranslated_terms": [
                {"term": term, "count": count} for term, count in untranslated_terms.most_common(20)
            ],
            "statuses": dict(counter),
        }

    def _filter_items_by_translation_status(
        self,
        items: list[dict[str, Any]],
        translation_status: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        normalized = str(translation_status or "").strip()
        if not normalized or normalized == "all":
            return items[:limit]
        if normalized == "untranslated":
            return [
                item
                for item in items
                if self._translation_status_for_item(item) not in {"translated", "not_required"}
            ][:limit]
        return [item for item in items if self._translation_status_for_item(item) == normalized][:limit]

    def _translation_status_for_item(self, item: dict[str, Any]) -> str:
        translation = item.get("translation", {})
        source_language = self._item_source_language(item, translation if isinstance(translation, dict) else {})
        if isinstance(translation, dict):
            status = str(translation.get("status", "") or "").strip()
            if status:
                if status == "not_required" and not is_chinese_language(source_language):
                    if (
                        translation.get("translated_title")
                        or translation.get("translated_summary")
                        or translation.get("translated_tags")
                        or translation.get("translated_risk_flags")
                    ):
                        return "translated"
                    if translation.get("untranslated_terms"):
                        return "missing_terms"
                    return "untranslated"
                return status
            if translation.get("translated_title"):
                return "translated"
        if is_chinese_language(source_language):
            return "not_required"
        return "untranslated"

    def _item_source_language(self, item: dict[str, Any], translation: dict[str, Any] | None = None) -> str:
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
        translation = translation if isinstance(translation, dict) else {}
        explicit_language = (
            translation.get("source_language")
            or metadata.get("language")
            or metadata.get("lang")
            or item.get("lang")
        )
        return infer_language(
            str(item.get("title") or item.get("item_title") or ""),
            str(item.get("canonical_text") or item.get("item_canonical_text") or item.get("summary") or ""),
            explicit_language,
        )

    def _normalized_translation_for_item(self, item: dict[str, Any], translation: Any) -> dict[str, Any]:
        if not isinstance(translation, dict):
            return {}
        normalized = dict(translation)
        source_language = self._item_source_language(item, normalized)
        if source_language:
            normalized["source_language"] = source_language
        if normalized.get("status") == "not_required" and not is_chinese_language(source_language):
            if (
                normalized.get("translated_title")
                or normalized.get("translated_summary")
                or normalized.get("translated_tags")
                or normalized.get("translated_risk_flags")
            ):
                normalized["status"] = "translated"
            elif normalized.get("untranslated_terms"):
                normalized["status"] = "missing_terms"
            else:
                normalized["status"] = "untranslated"
        return normalized

    def _decorate_event_groups(self, items: list[dict[str, Any]], collapse: bool = False) -> list[dict[str, Any]]:
        return decorate_event_groups(items, self._latest_insight_for_item, collapse=collapse)

    def _decorate_single_item_event(self, item: dict[str, Any]) -> dict[str, Any]:
        return decorate_single_item_event(item, self._event_candidate_items(), self._latest_insight_for_item)

    def _event_candidate_items(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT i.*
            FROM items i
            JOIN sources s ON s.id = i.source_id
            WHERE s.enabled = 1
            ORDER BY i.published_at DESC, i.created_at DESC
            LIMIT ?
            """,
            (EVENT_CONTEXT_LIMIT,),
        ).fetchall()
        return [self._item_row(row) for row in rows]

    def list_events(self, limit: int = 50) -> list[dict[str, Any]]:
        clean_limit = self._clean_limit(limit)
        groups = event_groups_for_items(self._event_candidate_items(), self._latest_insight_for_item)
        events = [event_group_payload(group, self._latest_insight_for_item) for group in groups if group]
        events.sort(key=lambda event: str(event.get("event_latest_at") or ""), reverse=True)
        return events[:clean_limit]

    def get_event(self, event_key: str) -> dict[str, Any] | None:
        normalized = str(event_key or "").strip()
        if not normalized:
            return None
        groups = event_groups_for_items(self._event_candidate_items(), self._latest_insight_for_item)
        for group in groups:
            payload = event_group_payload(group, self._latest_insight_for_item)
            if payload.get("event_key") == normalized:
                return payload
        return None

    def _latest_insight_for_item(self, item_id: str) -> dict[str, Any] | None:
        if not item_id:
            return None
        row = self.connection.execute(
            "SELECT * FROM insights WHERE item_id = ? ORDER BY created_at DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        return self._insight_row(row) if row else None

    def _has_recent_event_notification(self, item_id: str) -> bool:
        target_row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not target_row:
            return False
        target = self._item_row(target_row)
        rows = self.connection.execute(
            """
            SELECT i.*
            FROM notifications n
            JOIN items i ON i.id = n.item_id
            WHERE n.item_id IS NOT NULL
            ORDER BY n.created_at DESC
            LIMIT 100
            """
        ).fetchall()
        for row in rows:
            existing = self._item_row(row)
            if items_in_same_event(target, existing):
                return True
        return False

    def get_item(self, item_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return None
        item = self._item_row(row)
        item["assets"] = [self._asset_row(r) for r in self.connection.execute("SELECT * FROM assets WHERE item_id = ?", (item_id,))]
        item["extractions"] = [
            self._extraction_row(r) for r in self.connection.execute("SELECT * FROM extractions WHERE item_id = ?", (item_id,))
        ]
        insights = [self._insight_row(r) for r in self.connection.execute("SELECT * FROM insights WHERE item_id = ?", (item_id,))]
        item["insight"] = insights[-1] if insights else None
        item["favorite"] = self.connection.execute("SELECT id FROM favorites WHERE item_id = ?", (item_id,)).fetchone() is not None
        return self._decorate_single_item_event(item)

    def get_item_model(self, item_id: str) -> Item | None:
        row = self.connection.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._item_from_row(row) if row else None

    def list_favorites(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT f.*, i.title, i.source_url, i.score
            FROM favorites f
            JOIN items i ON i.id = f.item_id
            ORDER BY f.created_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def list_collections(self) -> list[dict[str, Any]]:
        return [self._collection_row(row) for row in self.connection.execute("SELECT * FROM collections ORDER BY created_at DESC")]

    def list_watchlists(self) -> list[dict[str, Any]]:
        return [self._watchlist_row(row) for row in self.connection.execute("SELECT * FROM watchlists ORDER BY created_at DESC")]

    def list_saved_searches(self) -> list[dict[str, Any]]:
        return [self._saved_search_row(row) for row in self.connection.execute("SELECT * FROM saved_searches ORDER BY created_at DESC")]

    def list_alert_rules(self) -> list[dict[str, Any]]:
        return [self._alert_rule_row(row) for row in self.connection.execute("SELECT * FROM alert_rules ORDER BY created_at DESC")]

    def list_notifications(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
              n.*,
              i.title AS item_title,
              i.source_id AS source_id,
              i.source_url AS source_url,
              i.canonical_text AS item_canonical_text,
              i.lang AS item_lang,
              i.score AS score,
              i.tags AS item_tags,
              i.metadata AS item_metadata,
              s.name AS source_name,
              latest_insight.summary AS insight_summary,
              latest_insight.risk_flags AS insight_risk_flags
            FROM notifications n
            LEFT JOIN items i ON i.id = n.item_id
            LEFT JOIN sources s ON s.id = i.source_id
            LEFT JOIN insights latest_insight ON latest_insight.id = (
              SELECT id FROM insights
              WHERE item_id = i.id
              ORDER BY created_at DESC
              LIMIT 1
            )
            ORDER BY n.created_at DESC
            """
        ).fetchall()
        notifications = [self._notification_row(row) for row in rows]
        for notification in notifications:
            self._decorate_notification_event(notification)
        return notifications

    def _decorate_notification_event(self, notification: dict[str, Any]) -> None:
        item_id = str(notification.get("item_id") or "")
        if not item_id:
            notification.setdefault("event_key", "")
            notification.setdefault("event_group", {})
            notification.setdefault("related_count", 0)
            notification.setdefault("related_items", [])
            notification.setdefault("source_count", 0)
            notification.setdefault("event_sources", [])
            notification.setdefault("event_latest_at", "")
            notification.setdefault("event_score", notification.get("score") or 0)
            notification.setdefault("event_evidence_refs", [])
            notification.setdefault("event_merge_reason", "系统消息")
            return
        item = self.get_item(item_id)
        if not item:
            return
        for key in (
            "event_key",
            "event_group",
            "related_count",
            "related_items",
            "source_count",
            "event_sources",
            "event_latest_at",
            "event_score",
            "event_evidence_refs",
            "event_merge_reason",
        ):
            notification[key] = item.get(key)

    def list_task_runs(self) -> list[dict[str, Any]]:
        return [self._task_run_row(row) for row in self.connection.execute("SELECT * FROM task_runs ORDER BY started_at DESC")]

    def health(self) -> dict[str, Any]:
        item_count = self.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        source_count = self.connection.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        return {
            "ok": True,
            "database": str(self.database_path),
            "items": item_count,
            "sources": source_count,
            "search": {"fts5_enabled": self.fts5_enabled, "fallback": "LIKE"},
        }

    def _index_item(self, item: Item) -> None:
        self.connection.execute("DELETE FROM item_search WHERE item_id = ?", (item.id,))
        self.connection.execute(
            "INSERT INTO item_search (item_id, title, canonical_text, entities, tags, source_url, author) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item.id,
                item.title,
                item.canonical_text,
                " ".join(item.entities),
                " ".join(item.tags),
                item.source_url,
                item.author or "",
            ),
        )

    def _index_item_row(self, row: sqlite3.Row) -> None:
        item = self._item_row(row)
        self.connection.execute("DELETE FROM item_search WHERE item_id = ?", (item["id"],))
        self.connection.execute(
            "INSERT INTO item_search (item_id, title, canonical_text, entities, tags, source_url, author) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                item["id"],
                item["title"],
                item["canonical_text"],
                " ".join(item["entities"]),
                " ".join(item["tags"]),
                item["source_url"],
                item["author"] or "",
            ),
        )

    def _source_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["tags"] = loads(result["tags"], [])
        result["enabled"] = bool(result["enabled"])
        result["metadata"] = loads(result.get("metadata"), {}) if hasattr(result, "get") else loads(result["metadata"], {})
        metadata = result["metadata"]
        for key in (
            "description",
            "publisher",
            "region",
            "market",
            "language",
            "quality_tier",
            "include_keywords",
            "exclude_keywords",
            "default_topics",
            "rsshub_base_url",
            "rsshub_route",
            "rsshub_url",
            "rsshub_healthcheck_path",
            "rsshub_check_health",
            "rsshub_instance_name",
            "json_mapping",
            "html_mapping",
        ):
            default = (
                []
                if key.endswith("keywords") or key == "default_topics"
                else False
                if key == "rsshub_check_health"
                else {}
                if key in {"json_mapping", "html_mapping"}
                else ""
            )
            result[key] = metadata.get(key, default)
        collectability = self._source_collectability(result)
        result["collectable"] = collectability["collectable"]
        result["collectability_status"] = collectability["status"]
        result["collectability_label"] = collectability["label"]
        result["filter_summary"] = {
            "include_keywords": result["include_keywords"],
            "exclude_keywords": result["exclude_keywords"],
            "default_topics": result["default_topics"],
        }
        return result

    def _source_collectability(self, source: dict[str, Any]) -> dict[str, Any]:
        source_type = str(source.get("source_type", "")).lower()
        location = str(source.get("location", "") or "").strip()
        if source_type == "rss":
            if location:
                return {"collectable": True, "status": "collectable", "label": "可采集 RSS"}
            return {"collectable": False, "status": "missing_url", "label": "缺少 RSS URL，暂不采集"}
        if source_type == "rsshub":
            has_rsshub_route = bool(str(source.get("rsshub_base_url", "")).strip() and str(source.get("rsshub_route", "")).strip())
            if location or has_rsshub_route:
                return {"collectable": True, "status": "collectable", "label": "可采集 RSSHub"}
            return {"collectable": False, "status": "missing_rsshub_route", "label": "缺少 RSSHub base/route，暂不采集"}
        if source_type == "json":
            if isinstance(source.get("json_mapping"), dict) and source.get("json_mapping"):
                return {"collectable": True, "status": "collectable", "label": "可采集 JSON"}
            return {"collectable": False, "status": "mapping_required", "label": "需要配置 JSON mapping，暂不直接采集"}
        if source_type == "html":
            if isinstance(source.get("html_mapping"), dict) and source.get("html_mapping"):
                return {"collectable": True, "status": "collectable", "label": "可采集 HTML"}
            return {"collectable": False, "status": "mapping_required", "label": "需要配置 HTML mapping，暂不直接采集"}
        return {"collectable": False, "status": "directory_only", "label": "目录说明，不参与公开源采集"}

    def _glossary_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["enabled"] = bool(result["enabled"])
        return result

    def _source_display_metadata(self, source_id: str) -> dict[str, str]:
        if not source_id:
            return {}
        row = self.connection.execute("SELECT name, metadata FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            return {}
        metadata = loads(row["metadata"], {})
        return {
            "source_name": str(row["name"] or ""),
            "source_publisher": str(metadata.get("publisher", "")),
            "source_region": str(metadata.get("region", "")),
            "source_market": str(metadata.get("market", "")),
        }

    def _item_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["entities"] = loads(result["entities"], [])
        result["tags"] = loads(result["tags"], [])
        result["metadata"] = loads(result["metadata"], {})
        source_metadata = self._source_display_metadata(result.get("source_id", ""))
        result["source_name"] = result["metadata"].get("source_name") or source_metadata.get("source_name", "")
        result["source_publisher"] = result["metadata"].get("publisher") or source_metadata.get("source_publisher", "")
        result["source_region"] = result["metadata"].get("region") or source_metadata.get("source_region", "")
        result["source_market"] = result["metadata"].get("market") or source_metadata.get("source_market", "")
        result["translation"] = self._normalized_translation_for_item(result, result["metadata"].get("translation", {}))
        result["translation_status"] = self._translation_status_for_item(result)
        return result

    def _item_from_row(self, row: sqlite3.Row) -> Item:
        item = self._item_row(row)
        for key in (
            "translation",
            "translation_status",
            "source_name",
            "source_publisher",
            "source_region",
            "source_market",
        ):
            item.pop(key, None)
        return Item(**item)

    def _asset_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = loads(result["metadata"], {})
        return result

    def _extraction_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = loads(result["metadata"], {})
        return result

    def _insight_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["signals"] = loads(result["signals"], [])
        result["risk_flags"] = loads(result["risk_flags"], [])
        result["related_assets"] = loads(result["related_assets"], [])
        result["evidence_refs"] = loads(result["evidence_refs"], [])
        return result

    def _collection_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["item_ids"] = loads(result["item_ids"], [])
        return result

    def _watchlist_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["keywords"] = loads(result["keywords"], [])
        result["enabled"] = bool(result["enabled"])
        return result

    def _saved_search_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["query"] = loads(result["query"], {})
        return result

    def _alert_rule_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["keywords"] = loads(result["keywords"], [])
        result["enabled"] = bool(result["enabled"])
        return result

    def _task_run_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["metadata"] = loads(result.get("metadata"), {}) if hasattr(result, "get") else loads(result["metadata"], {})
        return result

    def _notification_row(self, row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        raw_metadata = result.pop("item_metadata", None)
        raw_tags = result.pop("item_tags", None)
        raw_risk_flags = result.pop("insight_risk_flags", None)
        item_canonical_text = result.pop("item_canonical_text", "") or ""
        item_lang = result.pop("item_lang", "") or ""
        item_metadata = loads(raw_metadata, {}) if raw_metadata else {}
        tags = loads(raw_tags, []) if raw_tags else []
        risk_flags = loads(raw_risk_flags, []) if raw_risk_flags else []
        raw_translation = item_metadata.get("translation", {}) if isinstance(item_metadata, dict) else {}
        item_context = {
            "title": result.get("item_title") or "",
            "canonical_text": item_canonical_text,
            "lang": item_lang,
            "metadata": item_metadata,
            "translation": raw_translation,
        }
        translation = self._normalized_translation_for_item(item_context, raw_translation)
        result["translated_title"] = translation.get("translated_title", "")
        result["translated_summary"] = translation.get("translated_summary", "")
        result["summary"] = result.pop("insight_summary", "") or ""
        if result.get("item_id"):
            item_context["summary"] = result["summary"]
            item_context["translation"] = translation
            result["translation_status"] = self._translation_status_for_item(item_context)
        else:
            result["translation_status"] = "system"
        result["tags"] = list(dict.fromkeys(list(translation.get("translated_tags", [])) + tags))
        result["risk_flags"] = list(dict.fromkeys(list(translation.get("translated_risk_flags", [])) + risk_flags))
        result["source_url"] = result.get("source_url") or ""
        result["source_id"] = result.get("source_id") or ""
        result["source_name"] = result.get("source_name") or ""
        result["item_title"] = result.get("item_title") or ""
        result["is_clickable"] = bool(result.get("item_id") and result["item_title"])
        result["detail_url"] = f"/api/items/{result['item_id']}" if result["is_clickable"] else ""
        result["system_note"] = "" if result["is_clickable"] else "这条消息未关联具体情报，无法打开情报详情。"
        return result

    def _coerce_bool(self, value: Any) -> bool:
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
