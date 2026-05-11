const state = {
  industryDomains: [],
  latest: [],
  events: [],
  searchResults: [],
  sources: [],
  collections: [],
  watchlists: [],
  savedSearches: [],
  alertRules: [],
  notifications: [],
  runtimeStatus: {},
  selectedCollectionItemId: "",
  selectedCollectionTitle: "",
  activeView: "industry",
  loadedViews: new Set()
};

const $ = (selector) => document.querySelector(selector);

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {})
      }
    });
  } catch (error) {
    throw new Error("无法连接本机服务，请确认后端已启动或网络可用。");
  }

  if (response.status === 401 || response.status === 403) {
    throw new Error("认证失败，请检查远程访问账号或密码。");
  }

  let payload = {};
  try {
    payload = await response.json();
  } catch (error) {
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
  }

  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function formatChinaTime(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  })
    .formatToParts(date)
    .reduce((result, part) => {
      if (part.type !== "literal") result[part.type] = part.value;
      return result;
    }, {});
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute} 北京时间`;
}

function sourceLabel(item) {
  const metadata = item.metadata || {};
  return item.source_name || metadata.source_name || metadata.publisher || item.source_id || "未知渠道";
}

function itemTranslation(item) {
  return item.translation || (item.metadata && item.metadata.translation) || {};
}

function normalizeLanguage(value) {
  const raw = String(value || "").trim().toLowerCase().replaceAll("_", "-");
  if (!raw) return "";
  if (raw.startsWith("zh") || raw.startsWith("cn") || raw.startsWith("chinese")) return "zh";
  if (raw.startsWith("en") || raw.startsWith("english")) return "en";
  return raw.split("-")[0];
}

function detectLanguageFromText(...parts) {
  const sample = parts.filter(Boolean).join("\n");
  const cjkCount = (sample.match(/[\u4e00-\u9fff]/g) || []).length;
  const latinWords = sample.match(/\b[A-Za-z]{3,}\b/g) || [];
  if (cjkCount >= 2) return "zh";
  if (latinWords.length >= 2) return "en";
  if (cjkCount) return "zh";
  if (latinWords.length) return "en";
  return "";
}

function itemLanguage(item, translation = itemTranslation(item)) {
  const metadata = item.metadata || {};
  const explicit = normalizeLanguage(translation.source_language || metadata.language || metadata.lang || item.lang);
  const detected = detectLanguageFromText(item.title || item.item_title || "", item.canonical_text || item.summary || item.message || "");
  if (explicit === "zh" && detected === "en") return "en";
  return explicit || detected || "zh";
}

function isChineseItem(item, translation = itemTranslation(item)) {
  return itemLanguage(item, translation) === "zh";
}

function displayTitle(item) {
  const translation = itemTranslation(item);
  return translation.translated_title || item.title;
}

function displaySummary(item) {
  const translation = itemTranslation(item);
  return translation.translated_summary || item.canonical_text;
}

function displayTags(item) {
  const translation = itemTranslation(item);
  return [
    ...new Set([
      ...(translation.translated_tags || []),
      ...(translation.translated_risk_flags || []),
      ...(item.tags || []),
      ...(item.entities || [])
    ])
  ].slice(0, 6);
}

function translationStatusLabel(status) {
  switch (status) {
    case "translated":
      return "已翻译";
    case "missing_terms":
      return "词典未命中";
    case "error":
      return "翻译失败";
    case "disabled":
      return "未启用翻译";
    case "not_required":
      return "原文中文";
    case "system":
      return "系统消息";
    case "missing":
    case "untranslated":
      return "待翻译";
    default:
      return status ? String(status) : "待翻译";
  }
}

function translationStatusKind(status) {
  if (status === "translated") return "ok";
  if (status === "error") return "error";
  if (status === "missing_terms" || status === "missing" || status === "untranslated") return "warning";
  return "muted";
}

function translationStatusOptions(includeAll = true) {
  const options = [
    ["untranslated", "未翻译"],
    ["missing_terms", "词典未命中"],
    ["translated", "已翻译"],
    ["error", "翻译失败"],
    ["disabled", "未启用翻译"]
  ];
  return includeAll ? [["", "全部翻译状态"], ...options] : options;
}

function renderTranslationStatusSelect(target, selectedValue, includeAll = true) {
  const select = $(target);
  if (!select) return;
  select.innerHTML = translationStatusOptions(includeAll)
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`)
    .join("");
  select.value = translationStatusOptions(includeAll).some(([value]) => value === selectedValue) ? selectedValue : "";
}

function translationState(item) {
  const translation = itemTranslation(item);
  const hasTranslatedText = Boolean(
    translation.translated_title ||
      translation.translated_summary ||
      (translation.translated_tags || []).length ||
      (translation.translated_risk_flags || []).length
  );
  const untranslated = translation.untranslated_terms || [];
  let status = translation.status;
  if (status === "not_required" && !isChineseItem(item, translation)) {
    status = hasTranslatedText ? "translated" : untranslated.length ? "missing_terms" : "untranslated";
  }
  if (!status && hasTranslatedText) status = "translated";
  if (!status && untranslated.length) status = "missing_terms";
  if (!status && isChineseItem(item, translation)) status = "not_required";
  if (!status) status = "untranslated";
  return {
    status,
    label: translationStatusLabel(status),
    kind: translationStatusKind(status),
    provider: translation.provider || ""
  };
}

function translationActionLabel(status) {
  switch (status) {
    case "translated":
      return "已自动翻译";
    case "missing_terms":
      return "查看未命中";
    case "error":
      return "重试翻译";
    case "disabled":
      return "翻译未启用";
    case "not_required":
      return "原文中文";
    case "system":
      return "系统消息";
    default:
      return "翻译为中文";
  }
}

function translationButtonLabel(item) {
  return translationActionLabel(translationState(item).status);
}

function translationStatusBadge(item) {
  const state = translationState(item);
  const provider = state.provider ? ` · ${state.provider}` : "";
  return `<span class="badge translation-status ${state.kind}">译：${escapeHtml(state.label + provider)}</span>`;
}

function translationFeedback(item) {
  const translation = itemTranslation(item);
  const translatedBits = [
    ...(translation.translated_tags || []),
    ...(translation.translated_risk_flags || []),
    ...(translation.translated_terms || [])
  ];
  const untranslated = translation.untranslated_terms || [];
  if (translation.status === "error") {
    return `<div class="translation-note error"><strong>翻译失败：</strong>${escapeHtml(translation.error || "本地词典处理失败。")}</div>`;
  }
  if (untranslated.length) {
    return `<div class="translation-note warning"><strong>词典未命中：</strong>${escapeHtml(untranslated.join("、"))}</div>`;
  }
  if (translation.status === "missing_terms") {
    return `<div class="translation-note warning"><strong>词典未命中：</strong>当前本地词典无法完整翻译，后续将由本地大模型自动翻译能力补齐。</div>`;
  }
  if (translatedBits.length) {
    return `<div class="translation-note"><strong>中文辅助：</strong>${escapeHtml([...new Set(translatedBits)].join("、"))}</div>`;
  }
  if (translation.status === "translated" && (translation.translated_title || translation.translated_summary)) {
    return `<div class="translation-note"><strong>本地词典译文已更新。</strong></div>`;
  }
  const state = translationState(item);
  if (state.status === "untranslated") {
    return `<div class="translation-note warning"><strong>待翻译：</strong>可点击“翻译为中文”，若词典未覆盖会列出未命中词。</div>`;
  }
  if (state.status === "disabled") {
    return `<div class="translation-note warning"><strong>翻译未启用：</strong>请在运行维护配置中启用本地词典。</div>`;
  }
  return "";
}

function eventBadge(item) {
  const relatedCount = Number(item.related_count || 0);
  const sourceCount = Number(item.source_count || 0);
  if (!relatedCount) return "";
  return `<span class="badge event-badge">同事件 ${sourceCount || relatedCount + 1} 个来源</span>`;
}

function relatedItemsBlock(item, compact = false) {
  const related = item.related_items || [];
  if (!related.length) return "";
  const sourceCount = Number(item.source_count || related.length + 1);
  const label = compact ? `同事件来源（${sourceCount}）` : `展开相关报道（${related.length}）`;
  return `
    <details class="related-items${compact ? " compact" : ""}">
      <summary>${escapeHtml(label)}</summary>
      <ul class="related-list">
        ${related
          .map((relatedItem) => {
            const title = relatedItem.translated_title || relatedItem.title || "相关报道";
            const source = relatedItem.source_name || "未知渠道";
            const time = formatChinaTime(relatedItem.published_at);
            const score = relatedItem.score === null || relatedItem.score === undefined ? "" : ` · ${Math.round(relatedItem.score)} 分`;
            return `
              <li>
                <a href="${escapeHtml(relatedItem.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>
                <span class="related-source">${escapeHtml(`${source}${time ? ` · ${time}` : ""}${score}`)}</span>
              </li>
            `;
          })
          .join("")}
      </ul>
    </details>
  `;
}

function eventExplanationBlock(event) {
  const tokens = event.matched_tokens || [];
  const topics = event.matched_topics || [];
  const timeWindow = event.time_window || {};
  const guard = event.conflict_guard || {};
  const conflicts = guard.matched_conflicts || [];
  return `
    <div class="event-explain">
      <div><strong>归并规则：</strong>${escapeHtml(event.event_merge_reason || "单条情报")}</div>
      <div><strong>标题交集：</strong>${tokens.length ? escapeHtml(tokens.join("、")) : "无"}</div>
      <div><strong>主题交集：</strong>${topics.length ? escapeHtml(topics.join("、")) : "无"}</div>
      <div><strong>时间窗口：</strong>${escapeHtml(String(timeWindow.hours || 36))} 小时内，实际跨度 ${escapeHtml(timeWindow.actual_span_seconds === null || timeWindow.actual_span_seconds === undefined ? "未知" : `${Math.round(Number(timeWindow.actual_span_seconds) / 60)} 分钟`)}</div>
      <div><strong>冲突保护：</strong>${conflicts.length ? escapeHtml(conflicts.join("、")) : "未命中相反动作词"}</div>
    </div>
  `;
}

function eventSourceList(event) {
  const items = event.event_items || [];
  if (!items.length) return "<p>暂无来源明细。</p>";
  return `
    <ul class="event-source-list">
      ${items
        .map((item) => {
          const title = item.translated_title || item.title || "相关报道";
          const source = item.source_name || "未知渠道";
          const time = formatChinaTime(item.published_at);
          const score = item.score === null || item.score === undefined ? "" : ` · ${Math.round(item.score)} 分`;
          const tags = [...new Set([...(item.tags || []), ...(item.risk_flags || [])])].slice(0, 6);
          return `
            <li>
              <a href="${escapeHtml(item.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(title)}</a>
              <span class="related-source">${escapeHtml(`${source}${time ? ` · ${time}` : ""}${score}`)}</span>
              <div class="notification-context">${tags.map((tag) => `<span class="badge${(item.risk_flags || []).includes(tag) ? " risk" : ""}">${escapeHtml(tag)}</span>`).join("")}</div>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function eventCard(event) {
  const sources = event.event_sources || [];
  const items = event.event_items || [];
  const tags = [...new Set(items.flatMap((item) => item.tags || []))].slice(0, 6);
  const risks = [...new Set(items.flatMap((item) => item.risk_flags || []))].slice(0, 6);
  return `
    <article class="item-card event-card" data-open-event="${escapeHtml(event.event_key)}" tabindex="0">
      <h2>${escapeHtml(event.title || "未命名事件")}</h2>
      <p>${escapeHtml(event.event_summary || "暂无事件摘要。")}</p>
      <div class="card-meta">
        <span class="badge event-badge">同事件 ${Number(event.source_count || 0)} 个来源</span>
        <span class="badge score">${Math.round(event.event_score || 0)} 分</span>
        <span>${escapeHtml(formatChinaTime(event.event_latest_at))}</span>
        ${risks.map((tag) => `<span class="badge risk">${escapeHtml(tag)}</span>`).join("")}
        ${tags.map((tag) => `<span class="badge">${escapeHtml(tag)}</span>`).join("")}
      </div>
      <div class="event-explain compact">
        <strong>归并解释：</strong>${escapeHtml(event.event_merge_reason || "单条情报")}
        ${sources.length ? `<span> · 来源：${escapeHtml(sources.join("、"))}</span>` : ""}
      </div>
      <div class="card-actions">
        <button class="compact-button" type="button" data-event-open="${escapeHtml(event.event_key)}">事件详情</button>
      </div>
    </article>
  `;
}

function scoreValue(value) {
  return Math.round(Number(value || 0));
}

function industryDirectionKind(domain) {
  const direction = domain.signal_direction || "";
  if (direction.includes("利好") || direction.includes("偏强")) return "ok";
  if (direction.includes("利空") || direction.includes("偏弱") || domain.signal_level === "风险较高") return "error";
  if (direction.includes("热门") || domain.signal_level === "继续观察") return "warning";
  return "muted";
}

function industryScoreGrid(domain) {
  const items = [
    ["综合", domain.domain_score],
    ["热度", domain.attention_score],
    ["利好", domain.benefit_score],
    ["风险", domain.risk_score]
  ];
  return `
    <div class="industry-score-grid">
      ${items
        .map(
          ([label, value]) => `
            <div class="industry-score">
              <span>${escapeHtml(label)}</span>
              <strong>${scoreValue(value)}</strong>
            </div>
          `
        )
        .join("")}
    </div>
  `;
}

function keywordBadges(values, className = "") {
  return (values || [])
    .slice(0, 8)
    .map((value) => `<span class="badge ${className}">${escapeHtml(value)}</span>`)
    .join("");
}

function industryDomainCard(domain) {
  const kind = industryDirectionKind(domain);
  const catalysts = domain.main_catalysts || [];
  const risks = domain.risk_flags || [];
  return `
    <article class="item-card industry-card" data-open-industry="${escapeHtml(domain.domain_id)}" tabindex="0">
      <div class="industry-card-title">
        <span class="badge score">#${Number(domain.rank || 0) || "-"}</span>
        <h2>${escapeHtml(domain.domain_name || "未命名行业域")}</h2>
      </div>
      <p>${escapeHtml(domain.domain_description || "暂无行业域说明。")}</p>
      ${industryScoreGrid(domain)}
      <div class="card-meta">
        <span class="badge translation-status ${kind}">方向：${escapeHtml(domain.signal_direction || "中性")}</span>
        <span class="badge event-badge">研究优先级：${escapeHtml(domain.signal_level || "暂无明显信号")}</span>
        <span class="badge">行情确认：${escapeHtml(domain.market_confirmation || "未接入")}</span>
        <span>${escapeHtml(`${Number(domain.event_count || 0)} 个事件 · ${Number(domain.source_count || 0)} 个来源 · ${Number(domain.evidence_count || 0)} 条证据 · ${Number(domain.related_stock_count || 0)} 只监控样本`)}</span>
      </div>
      <div class="notification-context">
        ${keywordBadges(catalysts)}
        ${keywordBadges(risks, "risk")}
      </div>
      <div class="card-actions">
        <button class="compact-button" type="button" data-industry-open="${escapeHtml(domain.domain_id)}">行业域详情</button>
      </div>
    </article>
  `;
}

function industryEvidenceList(refs) {
  const evidence = refs || [];
  if (!evidence.length) return "<p>暂无证据。</p>";
  return `
    <ul class="industry-evidence-list">
      ${evidence
        .map((ref) => {
          const source = ref.source_name || "未知渠道";
          const time = formatChinaTime(ref.published_at);
          const terms = (ref.matched_terms || []).join("、");
          return `
            <li>
              <a href="${escapeHtml(ref.source_url || "#")}" target="_blank" rel="noreferrer">${escapeHtml(ref.title || "证据")}</a>
              <span class="related-source">${escapeHtml(`${source}${time ? ` · ${time}` : ""}${terms ? ` · 命中：${terms}` : ""}`)}</span>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function industryRelatedEvents(events) {
  const items = events || [];
  if (!items.length) return "<p>暂无相关事件。</p>";
  return `
    <ul class="industry-evidence-list">
      ${items
        .map(
          (event) => `
            <li>
              <button class="link-button" type="button" data-industry-event="${escapeHtml(event.event_key || "")}">${escapeHtml(event.title || "相关事件")}</button>
              <span class="related-source">${escapeHtml(`${Number(event.item_count || 0)} 条 · ${Number(event.source_count || 0)} 个来源 · ${formatChinaTime(event.latest_at)}`)}</span>
            </li>
          `
        )
        .join("")}
    </ul>
  `;
}

function industryRelatedStocks(stocks, status) {
  const items = stocks || [];
  if (!items.length) {
    return `<p class="muted">${escapeHtml(status || "暂无关联监控股票池结果。")}</p>`;
  }
  return `
    <div class="related-stocks-top10" data-related-stocks-top10="true">
      ${items
        .map((stock) => {
          const metrics = stock.monitoring_metrics || {};
          const reasons = stock.match_reasons || [];
          const concepts = stock.matched_concepts || [];
          const industries = stock.matched_industry_tags || [];
          const keywords = stock.matched_keywords || [];
          const evidence = stock.evidence_refs || [];
          return `
            <article class="stock-card">
              <div class="stock-card-title">
                <span class="badge score">#${Number(stock.association_rank || 0) || "-"}</span>
                <div>
                  <h4>${escapeHtml(stock.stock_name || "未命名股票")}</h4>
                  <span>${escapeHtml(`${stock.exchange || ""} ${stock.stock_code || ""}`.trim())}</span>
                </div>
                <strong>${scoreValue(stock.association_score)} 分</strong>
              </div>
              <p class="muted">${escapeHtml(stock.research_role || "监控样本")}，用于观察该行业域持续性和后续数据验证。</p>
              <div class="notification-context">
                ${keywordBadges(industries)}
                ${keywordBadges(concepts)}
                ${keywordBadges(keywords)}
              </div>
              <ul class="stock-reasons">
                ${reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("") || "<li>等待更多公开证据。</li>"}
              </ul>
              <div class="event-explain compact">
                <div><strong>监控指标：</strong>行情 ${escapeHtml(metrics.market_data || "未接入")} · 资金 ${escapeHtml(metrics.capital_flow || "未接入")} · 财务 ${escapeHtml(metrics.financials || "未接入")} · 超额收益 ${escapeHtml(metrics.excess_return_validation || "待验证")}</div>
                ${stock.notes ? `<div>${escapeHtml(stock.notes)}</div>` : ""}
              </div>
              ${industryRelatedEvents(stock.related_events)}
              ${industryEvidenceList(evidence)}
            </article>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderIndustryDomains() {
  const el = $("#industryList");
  el.innerHTML = state.industryDomains.length
    ? state.industryDomains.map(industryDomainCard).join("")
    : empty("暂无行业域热度结果。请先采集公开源或刷新最新信息。");
  el.querySelectorAll("[data-open-industry]").forEach((node) => {
    node.addEventListener("click", () => guarded(null, () => openIndustryDomainDetail(node.dataset.openIndustry)));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter") guarded(null, () => openIndustryDomainDetail(node.dataset.openIndustry));
    });
  });
  el.querySelectorAll("[data-industry-open]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => openIndustryDomainDetail(button.dataset.industryOpen));
    });
  });
}

function setStatus(message, kind = "error") {
  const banner = $("#statusBanner");
  banner.hidden = false;
  banner.textContent = message;
  banner.className = `status-banner ${kind}`;
}

function clearStatus() {
  const banner = $("#statusBanner");
  banner.hidden = true;
  banner.textContent = "";
  banner.className = "status-banner";
}

function renderError(target, error) {
  const el = $(target);
  if (el) {
    el.innerHTML = `<div class="error">${escapeHtml(error.message)}</div>`;
  }
}

function itemCard(item) {
  const translation = itemTranslation(item);
  const tags = displayTags(item);
  const riskTags = tags.filter((tag) => ["风险", "监管", "制裁", "下调", "冲突"].includes(tag));
  const source = sourceLabel(item);
  return `
    <article class="item-card" data-open-item="${escapeHtml(item.id)}" tabindex="0">
      <h2>${escapeHtml(displayTitle(item))}</h2>
      ${translation.translated_title ? `<div class="original-title">原文：${escapeHtml(item.title)}</div>` : ""}
      <p>${escapeHtml(displaySummary(item)).slice(0, 150)}</p>
      ${translation.translated_summary ? `<div class="original-title">原文摘要：${escapeHtml(item.canonical_text).slice(0, 150)}</div>` : ""}
      ${translationFeedback(item)}
      <div class="card-meta">
        <span class="badge score">${Math.round(item.score || 0)} 分</span>
        <span class="badge source-badge">渠道：${escapeHtml(source)}</span>
        ${eventBadge(item)}
        ${translationStatusBadge(item)}
        ${riskTags.map((tag) => `<span class="badge risk">${escapeHtml(tag)}</span>`).join("")}
        ${tags.map((tag) => `<span class="badge">${escapeHtml(tag)}</span>`).join("")}
        <span>${escapeHtml(formatChinaTime(item.published_at))}</span>
      </div>
      ${relatedItemsBlock(item)}
      <div class="card-actions">
        <button class="compact-button" type="button" data-action="event" data-event-key="${escapeHtml(item.event_key || "")}">事件详情</button>
        <button class="secondary compact-button" type="button" data-action="favorite" data-item-id="${escapeHtml(item.id)}">收藏</button>
        <button class="compact-button" type="button" data-action="collection" data-item-id="${escapeHtml(item.id)}" data-item-title="${escapeHtml(displayTitle(item))}">加专题</button>
        <button class="compact-button translate-button" type="button" data-action="translate" data-item-id="${escapeHtml(item.id)}">${escapeHtml(translationButtonLabel(item))}</button>
      </div>
    </article>
  `;
}

function row(title, body = "", actions = "") {
  return `
    <div class="list-row">
      <div>
        <strong>${escapeHtml(title)}</strong>
        <span class="muted">${escapeHtml(body)}</span>
      </div>
      ${actions ? `<div class="row-actions">${actions}</div>` : ""}
    </div>
  `;
}

function notificationRow(item, index) {
  const title = item.translated_title || item.item_title || item.title;
  const originalTitle = item.translated_title && item.item_title ? `<span class="original-title">原文：${escapeHtml(item.item_title)}</span>` : "";
  const statusLabel = translationStatusLabel(item.translation_status);
  const statusKind = translationStatusKind(item.translation_status);
  const score = item.score === null || item.score === undefined || item.score === "" ? "" : `${Math.round(item.score)} 分 · `;
  const source = item.source_name || item.source_id || "系统";
  const createdAt = formatChinaTime(item.created_at);
  const summary = item.translated_summary || item.summary || item.message || "";
  const tags = [...new Set([...(item.tags || []), ...(item.risk_flags || [])])].slice(0, 8);
  const actions = item.is_clickable
    ? `
      <button class="compact-button" type="button" data-notification-open="${index}">详情</button>
      <button class="compact-button" type="button" data-notification-event="${escapeHtml(item.event_key || "")}">事件</button>
      <button class="compact-button translate-button" type="button" data-notification-translate="${escapeHtml(item.item_id)}">${escapeHtml(translationActionLabel(item.translation_status))}</button>
    `
    : `<span class="badge translation-status muted">系统消息，可查看但不可跳转到情报</span>`;
  return `
    <div class="list-row notification-row${item.is_clickable ? " clickable" : ""}" data-notification-index="${index}" tabindex="0">
      <div>
        <strong>${escapeHtml(title)}</strong>
        ${originalTitle}
        <span class="muted">${escapeHtml(`${score}渠道：${source}${createdAt ? ` · ${createdAt}` : ""}`)}</span>
        <div class="notification-summary"><strong>摘要：</strong>${escapeHtml(summary).slice(0, 180)}</div>
        ${eventBadge(item)}
        <div class="notification-context">
          ${tags.map((tag) => `<span class="badge${(item.risk_flags || []).includes(tag) ? " risk" : ""}">${escapeHtml(tag)}</span>`).join("")}
        </div>
        <span class="badge translation-status ${statusKind}">译：${escapeHtml(statusLabel)}</span>
        ${relatedItemsBlock(item, true)}
      </div>
      <div class="row-actions">${actions}</div>
    </div>
  `;
}

function empty(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function bindItemActions(container) {
  container.querySelectorAll("[data-open-item]").forEach((node) => {
    node.addEventListener("click", () => guarded(null, () => openDetail(node.dataset.openItem)));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter") guarded(null, () => openDetail(node.dataset.openItem));
    });
  });
  container.querySelectorAll("[data-action='favorite']").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => addFavorite(button.dataset.itemId));
    });
  });
  container.querySelectorAll("[data-action='collection']").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      openCollectionPanel(button.dataset.itemId, button.dataset.itemTitle);
    });
  });
  container.querySelectorAll("[data-action='translate']").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => translateItem(button.dataset.itemId));
    });
  });
  container.querySelectorAll("[data-action='event']").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => openEventDetail(button.dataset.eventKey));
    });
  });
  container.querySelectorAll(".related-items").forEach((node) => {
    node.addEventListener("click", (event) => event.stopPropagation());
    node.addEventListener("keydown", (event) => event.stopPropagation());
  });
}

function renderItems(target, items, emptyText) {
  const el = $(target);
  el.innerHTML = items.length ? items.map(itemCard).join("") : empty(emptyText);
  bindItemActions(el);
}

function searchQueryFromInputs() {
  const query = $("#searchInput").value.trim();
  const sourceId = $("#searchSourceSelect").value;
  const topic = $("#topicInput").value.trim();
  const translationStatus = $("#searchTranslationStatusSelect").value;
  const minScore = $("#minScoreInput").value.trim();
  const result = {};
  if (query) result.query = query;
  if (sourceId) result.source_id = sourceId;
  if (topic) result.topic = topic;
  if (translationStatus) result.translation_status = translationStatus;
  if (minScore) result.min_score = minScore;
  return result;
}

function applySearchQuery(query) {
  $("#searchInput").value = query.query || "";
  $("#searchSourceSelect").value = query.source_id || "";
  $("#topicInput").value = query.topic || query.tag || "";
  $("#searchTranslationStatusSelect").value = query.translation_status || "";
  $("#minScoreInput").value = query.min_score || "";
}

async function guarded(target, loader) {
  try {
    await loader();
    return true;
  } catch (error) {
    if (target) renderError(target, error);
    setStatus(error.message);
    return false;
  }
}

async function loadHealth() {
  const health = await api("/api/health");
  $("#healthText").textContent = `${health.items} 条情报 · ${health.sources} 个来源`;
}

async function loadIndustryDomains() {
  const payload = await api("/api/industry-domains?window_days=7&limit=5");
  state.industryDomains = payload.domains || [];
  $("#industryCount").textContent = `${state.industryDomains.length} 个`;
  renderIndustryDomains();
}

async function loadRuntimeStatus() {
  const runtime = await api("/api/runtime/status");
  state.runtimeStatus = runtime;
  renderRuntimeStatus();
}

function enabledLabel(value) {
  return value ? "已启用" : "未启用";
}

function renderRuntimeStatus() {
  const runtime = state.runtimeStatus || {};
  const health = runtime.health || {};
  const scheduler = runtime.scheduler || {};
  const cards = [
    ["后端", health.ok ? "正常" : "异常", `${health.items || 0} 条情报 · ${health.sources || 0} 个来源`],
    ["认证", enabledLabel(runtime.auth_enabled), runtime.auth_enabled ? "Basic Auth 保护静态 PWA 和 API" : "本机默认无认证"],
    ["公开源", runtime.public_sources_configured ? "已配置" : "未配置", runtime.ingest_on_startup ? "启动时自动采集" : "可手动采集"],
    [
      "调度器",
      scheduler.enabled ? `${scheduler.interval_minutes} 分钟` : "未启用",
      scheduler.running ? "正在采集公开源" : `最近状态：${scheduler.last_status || "无"}`
    ]
  ];
  $("#runtimeStatusPanel").innerHTML = cards
    .map(
      ([label, value, body]) => `
        <div class="stat-card">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
          <small>${escapeHtml(body)}</small>
        </div>
      `
    )
    .join("");

  const remoteAccess = runtime.remote_access_enabled ? "远程访问保护已启用" : "远程访问保护未启用";
  const publicBaseUrl = runtime.public_base_url || "未配置 remote_public_base_url";
  $("#runtimeRemotePanel").innerHTML = [
    row("当前访问入口", runtime.current_access_url || "未识别当前入口"),
    row("受保护远程入口", `${publicBaseUrl} · ${remoteAccess}`),
    row("数据库", runtime.database_configured ? "已配置" : "未配置")
  ].join("");

  const runs = runtime.recent_task_runs || [];
  $("#runtimeTaskList").innerHTML = runs.length
    ? runs
        .map((item) =>
          row(
            `${item.task_type || "task"} · ${item.status}`,
            `${item.source_id} · found=${item.items_found} created=${item.items_created} filtered=${item.items_filtered || 0} · ${item.error || item.started_at || ""}`
          )
        )
        .join("")
    : empty("暂无任务记录。");
}

async function loadSources() {
  const currentLatestSource = $("#latestSourceSelect").value;
  const currentSearchSource = $("#searchSourceSelect").value;
  const payload = await api("/api/sources");
  state.sources = payload.sources;
  renderSourceSelect("#latestSourceSelect", currentLatestSource);
  renderSourceSelect("#searchSourceSelect", currentSearchSource);
  renderTranslationStatusSelect("#latestTranslationStatusSelect", $("#latestTranslationStatusSelect").value);
  renderTranslationStatusSelect("#searchTranslationStatusSelect", $("#searchTranslationStatusSelect").value);
  $("#sourceList").innerHTML = state.sources.length
    ? state.sources.map(sourceRow).join("")
    : empty("暂无数据源。");
  $("#sourceList").querySelectorAll("[data-source-id]").forEach((button) => {
    button.addEventListener("click", () =>
      guarded("#sourceList", () => toggleSource(button.dataset.sourceId, button.dataset.sourceEnabled !== "1"))
    );
  });
}

async function ensureSourcesLoaded(force = false) {
  if (force || !state.loadedViews.has("source-data")) {
    await loadSources();
    state.loadedViews.add("source-data");
  }
}

function renderSourceSelect(target, selectedValue) {
  const select = $(target);
  if (!select) return;
  const options = [
    `<option value="">全部数据源</option>`,
    ...state.sources.map((source) => `<option value="${escapeHtml(source.id)}">${escapeHtml(source.name)}</option>`)
  ];
  select.innerHTML = options.join("");
  select.value = state.sources.some((source) => source.id === selectedValue) ? selectedValue : "";
}

function sourceRow(source) {
  const rsshub = [
    source.rsshub_instance_name ? `实例：${source.rsshub_instance_name}` : "",
    source.rsshub_base_url ? `base：${source.rsshub_base_url}` : "",
    source.rsshub_route ? `route：${source.rsshub_route}` : ""
  ]
    .filter(Boolean)
    .join(" · ");
  const summary = [
    source.enabled ? "启用" : "停用",
    source.source_type,
    source.collectability_label,
    source.publisher,
    source.region,
    source.market,
    source.quality_tier
  ]
    .filter(Boolean)
    .join(" · ");
  const filters = [
    (source.include_keywords || []).length ? `纳入：${source.include_keywords.join("、")}` : "",
    (source.exclude_keywords || []).length ? `排除：${source.exclude_keywords.join("、")}` : "",
    (source.default_topics || []).length ? `主题：${source.default_topics.join("、")}` : "",
    rsshub,
    source.last_error ? `最近失败：${source.last_error}` : ""
  ]
    .filter(Boolean)
    .join(" · ");
  return row(
    source.name,
    `${summary}${source.location ? ` · ${source.location}` : ""}${source.description ? ` · ${source.description}` : ""}${filters ? ` · ${filters}` : ""}`,
    `<button class="compact-button" type="button" data-source-id="${escapeHtml(source.id)}" data-source-enabled="${source.enabled ? "1" : "0"}">${source.enabled ? "停用" : "启用"}</button>`
  );
}

async function toggleSource(sourceId, enabled) {
  await api(`/api/sources/${sourceId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled })
  });
  await ensureSourcesLoaded(true);
  state.loadedViews.delete("latest");
  state.loadedViews.delete("events");
  state.loadedViews.delete("industry");
  if (["latest", "events", "industry"].includes(state.activeView)) {
    await loadView(state.activeView, true);
  }
  setStatus(enabled ? "数据源已启用。" : "数据源已停用。", "ok");
}

function resetSourceForm() {
  $("#sourceNameInput").value = "";
  $("#sourceTypeSelect").value = "rss";
  $("#sourceLocationInput").value = "";
  $("#sourceRssHubBaseInput").value = "";
  $("#sourceRssHubRouteInput").value = "";
  $("#sourceTagsInput").value = "";
  $("#sourceDescriptionInput").value = "";
  $("#sourcePublisherInput").value = "";
  $("#sourceRegionInput").value = "";
  $("#sourceMarketInput").value = "";
  $("#sourceLanguageInput").value = "";
  $("#sourceQualityTierInput").value = "";
  $("#sourceIncludeKeywordsInput").value = "";
  $("#sourceExcludeKeywordsInput").value = "";
  $("#sourceDefaultTopicsInput").value = "";
  $("#sourceEnabledInput").checked = true;
}

async function createSource(event) {
  event.preventDefault();
  const name = $("#sourceNameInput").value.trim();
  const location = $("#sourceLocationInput").value.trim();
  const sourceType = $("#sourceTypeSelect").value;
  if (!name) return;
  await api("/api/sources", {
    method: "POST",
    body: JSON.stringify({
      name,
      source_type: sourceType,
      location,
      tags: splitKeywords($("#sourceTagsInput").value),
      enabled: $("#sourceEnabledInput").checked,
      description: $("#sourceDescriptionInput").value.trim(),
      publisher: $("#sourcePublisherInput").value.trim(),
      region: $("#sourceRegionInput").value.trim(),
      market: $("#sourceMarketInput").value.trim(),
      language: $("#sourceLanguageInput").value.trim(),
      quality_tier: $("#sourceQualityTierInput").value.trim(),
      rsshub_base_url: $("#sourceRssHubBaseInput").value.trim(),
      rsshub_route: $("#sourceRssHubRouteInput").value.trim(),
      rsshub_healthcheck_path: "/healthz",
      rsshub_check_health: true,
      rsshub_instance_name: "local-rsshub",
      include_keywords: splitKeywords($("#sourceIncludeKeywordsInput").value),
      exclude_keywords: splitKeywords($("#sourceExcludeKeywordsInput").value),
      default_topics: splitKeywords($("#sourceDefaultTopicsInput").value)
    })
  });
  resetSourceForm();
  await ensureSourcesLoaded(true);
  state.loadedViews.delete("runtime");
  state.loadedViews.delete("events");
  state.loadedViews.delete("industry");
  const collectableTypes = ["rss", "rsshub"];
  setStatus(
    collectableTypes.includes(sourceType)
      ? "数据源已新增，可在运行页立即采集公开源。"
      : "数据源已新增为目录说明；JSON/HTML 需要 mapping 后才会参与采集。",
    "ok"
  );
}

async function loadLatest() {
  await ensureSourcesLoaded();
  const params = new URLSearchParams({ limit: "30" });
  const sourceId = $("#latestSourceSelect").value;
  const translationStatus = $("#latestTranslationStatusSelect").value;
  if (sourceId) params.set("source_id", sourceId);
  if (translationStatus) params.set("translation_status", translationStatus);
  const payload = await api(`/api/items/latest?${params.toString()}`);
  state.latest = payload.items;
  $("#latestCount").textContent = `${payload.items.length} 条`;
  renderItems("#latestList", payload.items, "暂无情报。请先运行 fixture 采集脚本。");
}

function renderEvents(target, events, emptyText) {
  const el = $(target);
  el.innerHTML = events.length ? events.map(eventCard).join("") : empty(emptyText);
  el.querySelectorAll("[data-open-event]").forEach((node) => {
    node.addEventListener("click", () => guarded(null, () => openEventDetail(node.dataset.openEvent)));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter") guarded(null, () => openEventDetail(node.dataset.openEvent));
    });
  });
  el.querySelectorAll("[data-event-open]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => openEventDetail(button.dataset.eventOpen));
    });
  });
}

async function loadEvents() {
  const payload = await api("/api/events?limit=20");
  state.events = payload.events || [];
  $("#eventCount").textContent = `${state.events.length} 组`;
  renderEvents("#eventList", state.events, "暂无事件组。请先采集公开源或刷新最新流。");
}

async function refreshLatestFeed(event) {
  if (event) event.preventDefault();
  const button = $("#latestRefreshButton");
  if (button) button.disabled = true;
  setStatus("正在刷新最新信息。", "ok");
  let ingest = null;
  let ingestError = null;
  try {
    try {
      const payload = await api("/api/tasks/ingest-public", {
        method: "POST",
        body: JSON.stringify({})
      });
      ingest = payload.ingest || {};
    } catch (error) {
      ingestError = error;
    }
    await loadHealth();
    await loadIndustryDomains();
    await loadLatest();
    state.loadedViews.delete("events");
    state.loadedViews.delete("alerts");
    if (state.searchResults.length) await runSearch();
    try {
      await loadRuntimeStatus();
    } catch (error) {
      // 最新流刷新不能因为运行状态面板不可用而失败。
    }
    if (ingestError) {
      setStatus(`已重新加载最新流；公开源采集未完成：${ingestError.message}`, "error");
      return;
    }
    const kind = ingest && (ingest.status === "success" || ingest.status === "running") ? "ok" : "error";
    const message =
      ingest && ingest.status === "running"
        ? ingest.message
        : `最新信息已刷新：新增 ${(ingest && ingest.items_created) || 0} 条，失败信息 ${(ingest && ingest.error) || "无"}。`;
    setStatus(message, kind);
  } finally {
    if (button) button.disabled = false;
  }
}

async function runSearch(event) {
  if (event) event.preventDefault();
  const params = new URLSearchParams(searchQueryFromInputs());
  const payload = await api(`/api/items/search?${params.toString()}`);
  state.searchResults = payload.items;
  renderItems("#searchList", payload.items, "没有匹配结果。");
  clearStatus();
}

async function loadSavedSearches() {
  const payload = await api("/api/saved-searches");
  state.savedSearches = payload.saved_searches;
  $("#savedSearchList").innerHTML = state.savedSearches.length
    ? state.savedSearches
        .map((item, index) =>
          row(
            item.name,
            Object.entries(item.query || {})
              .map(([key, value]) => `${key}=${value}`)
              .join(" · "),
            `<button class="compact-button" type="button" data-saved-search-index="${index}">恢复</button>`
          )
        )
        .join("")
    : empty("暂无保存搜索。");
  $("#savedSearchList").querySelectorAll("[data-saved-search-index]").forEach((button) => {
    button.addEventListener("click", () => {
      const saved = state.savedSearches[Number(button.dataset.savedSearchIndex)];
      applySearchQuery(saved.query || {});
      guarded("#searchList", () => runSearch());
    });
  });
}

async function createSavedSearch(event) {
  event.preventDefault();
  const query = searchQueryFromInputs();
  const name = $("#savedSearchName").value.trim() || query.query || query.topic || "未命名搜索";
  await api("/api/saved-searches", {
    method: "POST",
    body: JSON.stringify({ name, query })
  });
  $("#savedSearchName").value = "";
  await loadSavedSearches();
  setStatus("搜索已保存。", "ok");
}

async function loadCollections() {
  const [watchlists, collections] = await Promise.all([api("/api/watchlists"), api("/api/collections")]);
  state.watchlists = watchlists.watchlists;
  state.collections = collections.collections;
  $("#watchlistList").innerHTML = state.watchlists.length
    ? state.watchlists.map((item) => row(item.name, (item.keywords || []).join("、"))).join("")
    : empty("暂无观察清单。");
  $("#collectionList").innerHTML = state.collections.length
    ? state.collections.map((item) => row(item.name, `${item.item_ids.length} 条 · ${item.description || "无备注"}`)).join("")
    : empty("暂无专题集合。");
  renderCollectionSelect();
}

async function createCollection(event) {
  event.preventDefault();
  const name = $("#collectionName").value.trim();
  const description = $("#collectionDescription").value.trim();
  if (!name) return;
  await api("/api/collections", {
    method: "POST",
    body: JSON.stringify({ name, description, item_ids: [] })
  });
  $("#collectionName").value = "";
  $("#collectionDescription").value = "";
  await loadCollections();
  setStatus("专题已创建。", "ok");
}

async function loadFavorites() {
  const payload = await api("/api/favorites");
  $("#favoriteList").innerHTML = payload.favorites.length
    ? payload.favorites.map((item) => row(item.title, `${Math.round(item.score)} 分 · ${item.note || "无备注"}`)).join("")
    : empty("暂无收藏。");
}

async function loadAlerts() {
  const [notifications, taskRuns, alertRules] = await Promise.all([
    api("/api/notifications?limit=20"),
    api("/api/task-runs?limit=20"),
    api("/api/alert-rules")
  ]);
  state.alertRules = alertRules.alert_rules;
  state.notifications = notifications.notifications;
  $("#alertRuleList").innerHTML = state.alertRules.length
    ? state.alertRules
        .map((item) =>
          row(
            item.name,
            `${item.enabled ? "启用" : "停用"} · ${item.keywords.join("、")} · ${Math.round(item.min_score)} 分`,
            `<button class="compact-button" type="button" data-alert-rule-id="${escapeHtml(item.id)}" data-alert-rule-enabled="${item.enabled ? "1" : "0"}">${item.enabled ? "停用" : "启用"}</button>`
          )
        )
        .join("")
    : empty("暂无提醒规则。");
  $("#alertRuleList").querySelectorAll("[data-alert-rule-id]").forEach((button) => {
    button.addEventListener("click", () =>
      guarded("#alertRuleList", () => toggleAlertRule(button.dataset.alertRuleId, button.dataset.alertRuleEnabled !== "1"))
    );
  });
  $("#notificationList").innerHTML = notifications.notifications.length
    ? notifications.notifications.map(notificationRow).join("")
    : empty("暂无站内消息。");
  $("#notificationList").querySelectorAll("[data-notification-index]").forEach((node) => {
    node.addEventListener("click", () => guarded(null, () => openNotification(Number(node.dataset.notificationIndex))));
    node.addEventListener("keydown", (event) => {
      if (event.key === "Enter") guarded(null, () => openNotification(Number(node.dataset.notificationIndex)));
    });
  });
  $("#notificationList").querySelectorAll("[data-notification-open]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => openNotification(Number(button.dataset.notificationOpen)));
    });
  });
  $("#notificationList").querySelectorAll("[data-notification-translate]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => translateItem(button.dataset.notificationTranslate));
    });
  });
  $("#notificationList").querySelectorAll("[data-notification-event]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      guarded(null, () => openEventDetail(button.dataset.notificationEvent));
    });
  });
  $("#notificationList").querySelectorAll(".related-items").forEach((node) => {
    node.addEventListener("click", (event) => event.stopPropagation());
    node.addEventListener("keydown", (event) => event.stopPropagation());
  });
  $("#taskRunList").innerHTML = taskRuns.task_runs.length
    ? taskRuns.task_runs.map((item) => row(item.status, `found=${item.items_found} created=${item.items_created} ${item.error || ""}`)).join("")
    : empty("暂无任务记录。");
}

async function createAlertRule(event) {
  event.preventDefault();
  const name = $("#alertRuleName").value.trim();
  const keywords = splitKeywords($("#alertRuleKeywords").value);
  const minScore = $("#alertRuleMinScore").value.trim() || "60";
  if (!name || !keywords.length) return;
  await api("/api/alert-rules", {
    method: "POST",
    body: JSON.stringify({
      name,
      keywords,
      min_score: Number(minScore),
      enabled: $("#alertRuleEnabled").checked
    })
  });
  $("#alertRuleName").value = "";
  $("#alertRuleKeywords").value = "";
  $("#alertRuleMinScore").value = "";
  $("#alertRuleEnabled").checked = true;
  await loadAlerts();
  setStatus("提醒规则已创建。", "ok");
}

async function toggleAlertRule(alertRuleId, enabled) {
  await api(`/api/alert-rules/${alertRuleId}/toggle`, {
    method: "POST",
    body: JSON.stringify({ enabled })
  });
  await loadAlerts();
  setStatus(enabled ? "提醒规则已启用。" : "提醒规则已停用。", "ok");
}

function replaceStateItem(item) {
  state.latest = state.latest.map((existing) => (existing.id === item.id ? item : existing));
  state.searchResults = state.searchResults.map((existing) => (existing.id === item.id ? item : existing));
}

async function translateItem(itemId, refreshDetail = false) {
  if (!itemId) return;
  setStatus("正在使用本地词典翻译当前情报。", "ok");
  const payload = await api(`/api/items/${itemId}/translate`, {
    method: "POST",
    body: JSON.stringify({})
  });
  const item = payload.item;
  replaceStateItem(item);
  if (state.latest.length) renderItems("#latestList", state.latest, "暂无情报。请先运行 fixture 采集脚本。");
  if (state.searchResults.length) renderItems("#searchList", state.searchResults, "没有匹配结果。");
  await loadEvents();
  if ($("#detailPanel").classList.contains("open") || refreshDetail) {
    await openDetail(item.id);
  }
  await loadAlerts();
  const translation = itemTranslation(item);
  const state = translationState(item);
  const missingTerms = (translation.untranslated_terms || []).join("、");
  const visibleTranslation = [translation.translated_title, translation.translated_summary].filter(Boolean).join("；");
  const kind = state.status === "error" || state.status === "disabled" ? "error" : state.status === "missing_terms" || state.status === "untranslated" ? "warning" : "ok";
  const detail =
    visibleTranslation ||
    (missingTerms ? `未命中：${missingTerms}` : "") ||
    translation.error ||
    (state.status === "not_required" ? "当前情报已判断为中文原文。" : "暂无可展示译文。");
  setStatus(`翻译状态：${state.label}；${detail}`, kind);
}

async function runPublicIngest(event) {
  if (event) event.preventDefault();
  const button = $("#ingestPublicButton");
  button.disabled = true;
  setStatus("正在采集公开源。", "ok");
  try {
    const payload = await api("/api/tasks/ingest-public", {
      method: "POST",
      body: JSON.stringify({})
    });
    const ingest = payload.ingest || {};
    await loadRuntimeStatus();
    await ensureSourcesLoaded(true);
    state.loadedViews.delete("industry");
    state.loadedViews.delete("latest");
    state.loadedViews.delete("events");
    state.loadedViews.delete("alerts");
    await loadView(state.activeView, true);
    if (state.searchResults.length) await runSearch();
    const kind = ingest.status === "success" || ingest.status === "running" ? "ok" : "error";
    const message =
      ingest.status === "running"
        ? ingest.message
        : `公开源采集${ingest.status === "success" ? "完成" : "失败"}：新增 ${ingest.items_created || 0} 条，失败信息 ${ingest.error || "无"}。`;
    setStatus(message, kind);
  } finally {
    button.disabled = false;
  }
}

async function openIndustryDomainDetail(domainId) {
  if (!domainId) return;
  const payload = await api(`/api/industry-domains/${encodeURIComponent(domainId)}?window_days=7`);
  const domain = payload.domain;
  const explanation = domain.score_explanation || {};
  $("#detailContent").innerHTML = `
    <h2>行业域：${escapeHtml(domain.domain_name || "未命名行业域")}</h2>
    <p>${escapeHtml(domain.domain_description || "暂无行业域说明。")}</p>
    <div class="card-meta">
      <span class="badge score">综合 ${scoreValue(domain.domain_score)} 分</span>
      <span class="badge translation-status ${industryDirectionKind(domain)}">方向：${escapeHtml(domain.signal_direction || "中性")}</span>
      <span class="badge event-badge">研究优先级：${escapeHtml(domain.signal_level || "暂无明显信号")}</span>
      <span class="badge">行情确认：${escapeHtml(domain.market_confirmation || "未接入")}</span>
    </div>
    ${industryScoreGrid(domain)}
    <h3>分数拆解</h3>
    <div class="event-explain">
      <div><strong>事件数量：</strong>${scoreValue(explanation.event_count_score)} 分</div>
      <div><strong>热度增速：</strong>${scoreValue(explanation.velocity_score)} 分</div>
      <div><strong>来源广度：</strong>${scoreValue(explanation.source_breadth_score)} 分</div>
      <div><strong>来源质量：</strong>${scoreValue(explanation.source_quality_score)} 分</div>
      <div><strong>同事件确认：</strong>${scoreValue(explanation.same_event_confirmation_score)} 分</div>
      <div><strong>时间衰减：</strong>${scoreValue(explanation.recency_score)} 分</div>
      <div><strong>统计窗口：</strong>${escapeHtml(String(explanation.window_days || 7))} 天</div>
    </div>
    <h3>主要催化</h3>
    <div class="notification-context">${keywordBadges(domain.main_catalysts) || "<span class=\"muted\">暂无明确催化。</span>"}</div>
    <h3>风险提示</h3>
    <div class="notification-context">${keywordBadges(domain.risk_flags, "risk") || "<span class=\"muted\">暂无集中风险词。</span>"}</div>
    <h3>命中关键词</h3>
    <div class="notification-context">${keywordBadges(domain.matched_keywords) || "<span class=\"muted\">暂无。</span>"}</div>
    <h3>相关事件</h3>
    ${industryRelatedEvents(domain.related_events)}
    <h3>关联监控股票池 Top 10</h3>
    <p class="muted">以下仅为研究监控样本，用于观察行业方向持续性和后续超额收益验证。</p>
    ${industryRelatedStocks(domain.related_stocks_top10, domain.related_stock_status)}
    <h3>正面证据</h3>
    ${industryEvidenceList(domain.positive_evidence)}
    <h3>负面证据</h3>
    ${industryEvidenceList(domain.negative_evidence)}
    <h3>全部证据</h3>
    ${industryEvidenceList(domain.evidence_refs)}
    <h3>下一步观察点</h3>
    <ul>${(domain.next_observation_points || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("") || "<li>暂无</li>"}</ul>
  `;
  $("#detailContent").querySelectorAll("[data-industry-event]").forEach((button) => {
    button.addEventListener("click", () => guarded(null, () => openEventDetail(button.dataset.industryEvent)));
  });
  $("#detailPanel").classList.add("open");
}

async function openNotification(index) {
  const notification = state.notifications[index];
  if (!notification) return;
  if (notification.item_id && notification.is_clickable) {
    await openDetail(notification.item_id);
    return;
  }
  $("#detailContent").innerHTML = `
    <h2>${escapeHtml(notification.title || "系统消息")}</h2>
    <div class="card-meta">
      <span class="badge translation-status muted">系统消息</span>
      <span>${escapeHtml(formatChinaTime(notification.created_at))}</span>
      <span>${escapeHtml(notification.status || "")}</span>
    </div>
    <p>${escapeHtml(notification.message || "暂无消息内容。")}</p>
    <div class="original-block"><strong>跳转状态</strong><p>${escapeHtml(notification.system_note || "这条消息未关联具体情报，无法打开情报详情。")}</p></div>
  `;
  $("#detailPanel").classList.add("open");
}

async function openEventDetail(eventKey) {
  if (!eventKey) return;
  const payload = await api(`/api/events/${encodeURIComponent(eventKey)}`);
  const event = payload.event;
  $("#detailContent").innerHTML = `
    <h2>事件：${escapeHtml(event.title || "未命名事件")}</h2>
    <div class="card-meta">
      <span class="badge event-badge">同事件 ${Number(event.source_count || 0)} 个来源</span>
      <span class="badge score">${Math.round(event.event_score || 0)} 分</span>
      <span>${escapeHtml(formatChinaTime(event.event_latest_at))}</span>
      <span>${escapeHtml(event.event_key || "")}</span>
    </div>
    <p>${escapeHtml(event.event_summary || "暂无事件摘要。")}</p>
    <h3>归并解释</h3>
    ${eventExplanationBlock(event)}
    <h3>来源证据</h3>
    ${eventSourceList(event)}
    <h3>证据链接</h3>
    <ul>${(event.event_evidence_refs || []).map((ref) => `<li><a href="${escapeHtml(ref.url)}" target="_blank" rel="noreferrer">${escapeHtml(ref.source_name ? `${ref.source_name}：${ref.label}` : ref.label)}</a></li>`).join("") || "<li>暂无</li>"}</ul>
  `;
  $("#detailPanel").classList.add("open");
}

async function openDetail(itemId) {
  const payload = await api(`/api/items/${itemId}`);
  const item = payload.item;
  const insight = item.insight || {};
  const translation = itemTranslation(item);
  const translatedRisks = translation.translated_risk_flags || [];
  const translatedTags = translation.translated_tags || [];
  $("#detailContent").innerHTML = `
    <h2>${escapeHtml(displayTitle(item))}</h2>
    ${translation.translated_title ? `<div class="original-title">原文标题：${escapeHtml(item.title)}</div>` : ""}
    <div class="card-meta">
      <span class="badge score">${Math.round(item.score || 0)} 分</span>
      <span class="badge source-badge">渠道：${escapeHtml(sourceLabel(item))}</span>
      ${eventBadge(item)}
      ${translationStatusBadge(item)}
      <span>${escapeHtml(formatChinaTime(item.published_at))}</span>
      <span>${escapeHtml(item.source_type)}</span>
    </div>
    <div class="detail-actions">
      <button class="compact-button" type="button" data-detail-action="event" data-event-key="${escapeHtml(item.event_key || "")}">事件详情</button>
      <button class="secondary compact-button" type="button" data-detail-action="favorite" data-item-id="${escapeHtml(item.id)}">收藏</button>
      <button class="compact-button" type="button" data-detail-action="collection" data-item-id="${escapeHtml(item.id)}" data-item-title="${escapeHtml(displayTitle(item))}">加专题</button>
      <button class="compact-button translate-button" type="button" data-detail-action="translate" data-item-id="${escapeHtml(item.id)}">${escapeHtml(translationButtonLabel(item))}</button>
    </div>
    <h3>摘要</h3>
    <p>${escapeHtml(translation.translated_summary || insight.summary || "暂无摘要")}</p>
    ${translation.translated_summary ? `<div class="original-block"><strong>原文摘要</strong><p>${escapeHtml(insight.summary || "")}</p></div>` : ""}
    ${translationFeedback(item)}
    <h3>中文标签</h3>
    <ul>${translatedTags.length ? translatedTags.map((value) => `<li>${escapeHtml(value)}</li>`).join("") : "<li>暂无</li>"}</ul>
    <h3>信号</h3>
    <ul>${(insight.signals || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("") || "<li>暂无</li>"}</ul>
    <h3>风险提示</h3>
    <ul>${(translatedRisks.length ? translatedRisks : insight.risk_flags || []).map((value) => `<li>${escapeHtml(value)}</li>`).join("") || "<li>暂无</li>"}</ul>
    <h3>证据</h3>
    <ul>${(insight.evidence_refs || []).map((ref) => `<li><a href="${escapeHtml(ref.url)}" target="_blank" rel="noreferrer">${escapeHtml(ref.label)}</a></li>`).join("") || "<li>暂无</li>"}</ul>
    <h3>同事件相关报道</h3>
    ${relatedItemsBlock(item, true) || "<p>暂无其他来源报道。</p>"}
    <h3>正文</h3>
    <p>${escapeHtml(item.canonical_text)}</p>
  `;
  $("#detailContent")
    .querySelector("[data-detail-action='favorite']")
    .addEventListener("click", () => guarded(null, () => addFavorite(item.id)));
  $("#detailContent")
    .querySelector("[data-detail-action='collection']")
    .addEventListener("click", () => openCollectionPanel(item.id, displayTitle(item)));
  $("#detailContent")
    .querySelector("[data-detail-action='translate']")
    .addEventListener("click", () => guarded(null, () => translateItem(item.id, true)));
  $("#detailContent")
    .querySelector("[data-detail-action='event']")
    .addEventListener("click", (event) => guarded(null, () => openEventDetail(event.currentTarget.dataset.eventKey)));
  $("#detailPanel").classList.add("open");
}

async function addFavorite(itemId) {
  await api("/api/favorites", {
    method: "POST",
    body: JSON.stringify({ item_id: itemId, note: "从移动端收藏" })
  });
  await loadFavorites();
  setStatus("已加入收藏。", "ok");
}

function openCollectionPanel(itemId, title) {
  state.selectedCollectionItemId = itemId;
  state.selectedCollectionTitle = title || "";
  $("#collectionTargetText").textContent = state.selectedCollectionTitle;
  renderCollectionSelect();
  $("#collectionPanel").classList.add("open");
}

function closeCollectionPanel() {
  $("#collectionPanel").classList.remove("open");
}

function renderCollectionSelect() {
  const select = $("#collectionSelect");
  if (!select) return;
  select.innerHTML = state.collections.length
    ? state.collections.map((item) => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)} (${item.item_ids.length})</option>`).join("")
    : `<option value="">暂无专题</option>`;
  select.disabled = !state.collections.length;
}

async function addSelectedItemToCollection(event) {
  event.preventDefault();
  const collectionId = $("#collectionSelect").value;
  if (!collectionId || !state.selectedCollectionItemId) return;
  await api(`/api/collections/${collectionId}/items`, {
    method: "POST",
    body: JSON.stringify({ item_id: state.selectedCollectionItemId })
  });
  await loadCollections();
  closeCollectionPanel();
  setStatus("已加入专题。", "ok");
}

async function createCollectionForSelectedItem(event) {
  event.preventDefault();
  const name = $("#quickCollectionName").value.trim();
  if (!name || !state.selectedCollectionItemId) return;
  await api("/api/collections", {
    method: "POST",
    body: JSON.stringify({ name, description: "移动端创建", item_ids: [state.selectedCollectionItemId] })
  });
  $("#quickCollectionName").value = "";
  await loadCollections();
  closeCollectionPanel();
  setStatus("已新建专题并加入条目。", "ok");
}

async function createWatchlist(event) {
  event.preventDefault();
  const name = $("#watchlistName").value.trim();
  const keywords = splitKeywords($("#watchlistKeywords").value);
  if (!name || !keywords.length) return;
  await api("/api/watchlists", {
    method: "POST",
    body: JSON.stringify({ name, keywords })
  });
  $("#watchlistName").value = "";
  $("#watchlistKeywords").value = "";
  await loadCollections();
  setStatus("观察清单已创建。", "ok");
}

function splitKeywords(value) {
  return value
    .split(/[，,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

async function refreshAll() {
  const results = await Promise.all([guarded(null, loadHealth), guarded(null, () => loadView(state.activeView, true))]);
  if (results.every(Boolean)) clearStatus();
}

async function loadView(view, force = false) {
  if (!force && state.loadedViews.has(view)) return;
  if (view === "industry") {
    await loadIndustryDomains();
  } else if (view === "latest") {
    await loadLatest();
  } else if (view === "events") {
    await loadEvents();
  } else if (view === "source") {
    await ensureSourcesLoaded(force);
  } else if (view === "search") {
    await ensureSourcesLoaded(force);
    await loadSavedSearches();
  } else if (view === "collections") {
    await loadCollections();
  } else if (view === "favorites") {
    await loadFavorites();
  } else if (view === "alerts") {
    await loadAlerts();
  } else if (view === "runtime") {
    await loadRuntimeStatus();
  }
  state.loadedViews.add(view);
  if (view === "source") state.loadedViews.add("source-data");
}

async function refreshCurrentView() {
  const ok = await guarded(null, loadHealth);
  const viewOk = await guarded(null, () => loadView(state.activeView, true));
  if (ok && viewOk) {
    clearStatus();
  }
}

function switchView(view) {
  state.activeView = view;
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.view === view));
  document.querySelectorAll(".view").forEach((node) => node.classList.remove("active"));
  $(`#${view}View`).classList.add("active");
  guarded(null, () => loadView(view));
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchView(tab.dataset.view));
});

$("#refreshButton").addEventListener("click", refreshCurrentView);
$("#industryRefreshButton").addEventListener("click", () => guarded("#industryList", loadIndustryDomains));
$("#latestRefreshButton").addEventListener("click", (event) => guarded("#latestList", () => refreshLatestFeed(event)));
$("#eventRefreshButton").addEventListener("click", () => guarded("#eventList", loadEvents));
$("#latestSourceSelect").addEventListener("change", () => guarded("#latestList", loadLatest));
$("#latestTranslationStatusSelect").addEventListener("change", () => guarded("#latestList", loadLatest));
$("#sourceForm").addEventListener("submit", (event) => guarded("#sourceList", () => createSource(event)));
$("#searchForm").addEventListener("submit", (event) => guarded("#searchList", () => runSearch(event)));
$("#savedSearchForm").addEventListener("submit", (event) => guarded("#savedSearchList", () => createSavedSearch(event)));
$("#watchlistForm").addEventListener("submit", (event) => guarded("#watchlistList", () => createWatchlist(event)));
$("#collectionForm").addEventListener("submit", (event) => guarded("#collectionList", () => createCollection(event)));
$("#alertRuleForm").addEventListener("submit", (event) => guarded("#alertRuleList", () => createAlertRule(event)));
$("#ingestPublicButton").addEventListener("click", (event) => guarded("#runtimeTaskList", () => runPublicIngest(event)));
$("#existingCollectionForm").addEventListener("submit", (event) => guarded("#collectionList", () => addSelectedItemToCollection(event)));
$("#quickCollectionForm").addEventListener("submit", (event) => guarded("#collectionList", () => createCollectionForSelectedItem(event)));
$("#closeDetailButton").addEventListener("click", () => $("#detailPanel").classList.remove("open"));
$("#closeCollectionPanelButton").addEventListener("click", closeCollectionPanel);

window.addEventListener("offline", () => setStatus("当前离线，只能打开已缓存的页面外壳。"));
window.addEventListener("online", () => {
  clearStatus();
  refreshAll();
});

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/service-worker.js").catch(() => {});
}

refreshAll();
