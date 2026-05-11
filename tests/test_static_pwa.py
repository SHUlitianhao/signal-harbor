from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StaticPwaTest(unittest.TestCase):
    def test_mobile_workflow_entry_points_are_present(self) -> None:
        index_html = (ROOT / "frontend" / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
        styles_css = (ROOT / "frontend" / "static" / "styles.css").read_text(encoding="utf-8")

        for marker in [
            'id="savedSearchForm"',
            'id="savedSearchList"',
            'id="collectionPanel"',
            'id="alertRuleForm"',
            'id="alertRuleList"',
            'id="statusBanner"',
            'id="appVersion"',
            'data-app-version="1.6"',
            'data-view="industry"',
            'id="industryView"',
            'id="industryCount"',
            'id="industryRefreshButton"',
            'id="industryList"',
            "行业域热度",
            'id="latestRefreshButton"',
            'data-view="events"',
            'id="eventsView"',
            'id="eventCount"',
            'id="eventRefreshButton"',
            'id="eventList"',
            'id="latestSourceSelect"',
            'id="latestTranslationStatusSelect"',
            'id="searchSourceSelect"',
            'id="searchTranslationStatusSelect"',
            'data-view="source"',
            'id="sourceView"',
            'id="sourceForm"',
            'id="sourceList"',
            '<option value="rsshub">RSSHub</option>',
            "JSON/HTML 需要配置 mapping",
            'id="sourceRssHubBaseInput"',
            'id="sourceRssHubRouteInput"',
            'data-view="runtime"',
            'id="runtimeView"',
            'id="runtimeStatusPanel"',
            'id="runtimeRemotePanel"',
            'id="runtimeTaskList"',
            'id="ingestPublicButton"',
            "运行状态",
            "远程访问",
        ]:
            self.assertIn(marker, index_html)

        for marker in [
            "/api/saved-searches",
            "/api/industry-domains",
            "loadIndustryDomains",
            "loadView",
            "refreshCurrentView",
            "loadedViews",
            "industryDomainCard",
            "openIndustryDomainDetail",
            "industryScoreGrid",
            "industryEvidenceList",
            "market_confirmation",
            "related_stock_count",
            "related_stocks_top10",
            "industryRelatedStocks",
            "related-stocks-top10",
            "data-related-stocks-top10",
            "association_score",
            "match_reasons",
            "monitoring_metrics",
            "excess_return_validation",
            "关联监控股票池 Top 10",
            "研究监控样本",
            "attention_score",
            "benefit_score",
            "risk_score",
            "domain_score",
            "研究优先级",
            "行情确认",
            "/api/collections/${collectionId}/items",
            "/api/alert-rules/${alertRuleId}/toggle",
            "/api/sources/${sourceId}/toggle",
            'api("/api/sources"',
            "source_id",
            "无法连接本机服务",
            "认证失败，请检查远程访问账号或密码",
            "openCollectionPanel",
            "sourceRow",
            "createSource",
            "resetSourceForm",
            "collectability_label",
            "需要 mapping 后才会参与采集",
            "rsshub_base_url",
            "rsshub_route",
            "最近失败",
            "itemTranslation",
            "translationFeedback",
            "translationButtonLabel",
            "translationActionLabel",
            "translationStatusLabel",
            "translateItem",
            "/api/items/${itemId}/translate",
            "latestTranslationStatusSelect",
            "searchTranslationStatusSelect",
            "translation_status",
            "notificationRow",
            "formatChinaTime",
            "refreshLatestFeed",
            "latestRefreshButton",
            "最新信息已刷新",
            "sourceLabel",
            "北京时间",
            "渠道：",
            "openNotification",
            "data-notification-index",
            "data-notification-translate",
            "translated_title",
            "translated_summary",
            "translated_tags",
            "source_name",
            "risk_flags",
            "notification-summary",
            "notification-context",
            "eventBadge",
            "relatedItemsBlock",
            "event-badge",
            "related-items",
            "related-list",
            "eventCard",
            "loadEvents",
            "openEventDetail",
            "eventExplanationBlock",
            "eventSourceList",
            "/api/events",
            "data-open-event",
            "data-event-open",
            "event-explain",
            "event-source-list",
            "归并解释",
            "同事件",
            "展开相关报道",
            "同事件相关报道",
            "词典未命中",
            "待翻译",
            "未启用翻译",
            "翻译失败",
            "系统消息，可查看但不可跳转到情报",
            "原文标题",
            "/api/runtime/status",
            "/api/tasks/ingest-public",
            "loadRuntimeStatus",
            "runPublicIngest",
            "远程访问保护已启用",
            "无法连接本机服务",
            "认证失败，请检查远程访问账号或密码",
        ]:
            self.assertIn(marker, app_js)

        nav = index_html.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
        self.assertLess(nav.index('data-view="industry"'), nav.index('data-view="latest"'))
        self.assertIn('<button class="tab active" type="button" data-view="industry">行业域</button>', nav)
        industry_section = index_html.split('<section id="industryView"', 1)[1].split('<section id="latestView"', 1)[0]
        self.assertIn('class="view active"', index_html.split('<section id="industryView"', 1)[1].split(">", 1)[0])
        self.assertIn('id="industryList"', industry_section)

        latest_section = index_html.split('<section id="latestView"', 1)[1].split('<section id="sourceView"', 1)[0]
        self.assertNotIn('id="sourceList"', latest_section)
        self.assertNotIn('id="sourceForm"', latest_section)

        self.assertIn("@media (max-width: 720px)", styles_css)
        self.assertIn(".alert-form", styles_css)
        self.assertIn(".source-form", styles_css)
        self.assertIn(".status-banner", styles_css)
        self.assertIn(".app-version", styles_css)
        self.assertIn(".source-badge", styles_css)
        self.assertIn(".latest-refresh-button", styles_css)
        self.assertIn(".original-block", styles_css)
        self.assertIn(".translation-note", styles_css)
        self.assertIn(".notification-summary", styles_css)
        self.assertIn(".notification-context", styles_css)
        self.assertIn(".event-badge", styles_css)
        self.assertIn(".event-card", styles_css)
        self.assertIn(".event-explain", styles_css)
        self.assertIn(".event-source-list", styles_css)
        self.assertIn(".industry-card", styles_css)
        self.assertIn(".industry-score-grid", styles_css)
        self.assertIn(".industry-evidence-list", styles_css)
        self.assertIn(".related-stocks-top10", styles_css)
        self.assertIn(".stock-card", styles_css)
        self.assertIn(".stock-reasons", styles_css)
        self.assertIn(".related-items", styles_css)
        self.assertIn(".related-list", styles_css)
        self.assertIn(".related-source", styles_css)
        self.assertIn(".translate-button", styles_css)
        self.assertIn(".notification-row", styles_css)
        self.assertIn(".runtime-actions", styles_css)
        self.assertIn(".stats-grid", styles_css)
        self.assertIn(".stat-card", styles_css)
        self.assertIn("white-space: normal", styles_css)
        combined = "\n".join([index_html, app_js])
        self.assertNotIn("买入", combined)
        self.assertNotIn("卖出", combined)
        self.assertNotIn("仓位", combined)
        self.assertNotIn("建议买入", combined)
        self.assertNotIn("推荐买入", combined)
        self.assertNotIn("仓位建议", combined)

    def test_service_worker_keeps_api_out_of_shell_cache(self) -> None:
        service_worker = (ROOT / "frontend" / "static" / "service-worker.js").read_text(encoding="utf-8")

        self.assertIn('const CACHE_NAME = "signal-harbor-shell-v22"', service_worker)
        self.assertIn('if (url.pathname.startsWith("/api/")) return;', service_worker)

    def test_translation_main_tab_and_runtime_maintenance_entry_are_removed(self) -> None:
        index_html = (ROOT / "frontend" / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
        styles_css = (ROOT / "frontend" / "static" / "styles.css").read_text(encoding="utf-8")

        nav = index_html.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
        self.assertNotIn('data-view="translation"', nav)
        self.assertNotIn('id="translationView"', index_html)
        self.assertIn('data-view="runtime"', nav)
        self.assertNotIn("翻译维护", index_html)
        self.assertNotIn("/api/items/translate-batch", app_js)
        self.assertNotIn("loadTranslationAdmin", app_js)
        self.assertNotIn(".translation-form", styles_css)

    def test_translation_button_labels_do_not_show_original_chinese_for_english(self) -> None:
        app_js = (ROOT / "frontend" / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("translationActionLabel", app_js)
        self.assertIn("翻译为中文", app_js)
        self.assertIn('status === "not_required" && !isChineseItem', app_js)
        self.assertNotIn("翻译：${translationState(item).label}", app_js)
        self.assertNotIn("翻译：${escapeHtml(statusLabel)}", app_js)


if __name__ == "__main__":
    unittest.main()
