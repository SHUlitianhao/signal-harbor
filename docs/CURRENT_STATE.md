# Signal Harbor 当前状态

本文件是后续 agent 默认先读的短上下文入口。需要细节时再打开 `README.md`、`docs/ARCHITECTURE.md`、`docs/PRD.md` 和 `docs/ROADMAP.md`；不要默认读取 `docs/archive/`。

## 当前定位

- 本机自托管投资情报与行业域热度识别系统，面向单用户、Windows 本机常驻、Android 手机浏览器/PWA 访问。
- 不提供直接买卖建议；所有情报保留原始 URL、来源名称、发布时间和证据引用。
- RSSHub 是外部上游服务，Signal Harbor 只消费公开 RSS/RSSHub/RSS/JSON/HTML 来源，不并入 RSSHub 的 Node/TypeScript 代码。

## 当前能力

- Python 标准库后端、SQLite 本地持久化、静态 PWA。
- 公开源采集：fixture、RSS、RSSHub、JSON、低风险静态 HTML。
- RSSHub 首批来源：华尔街见闻重要快讯、财联社加红电报、金十重要快讯、同花顺重要要闻、DW 中文国际新闻。
- 运行闭环：本机服务、启动时可选采集、后台定时采集、PWA 运行页手动采集。
- 手机远程：受保护入口 + Basic Auth，外部安全通道由用户自行配置。
- 搜索、收藏、专题、提醒、数据源页、事件页、运行页。
- 行业域热度识别第一期：基于短期强催化和持续热点结合生成 A 股行业域热门榜；行业域详情已展示关联监控股票池 Top 10，用作研究监控样本。行情、资金、财务、一致预期确认和真实超额收益验证尚未接入。
- 性能优化：PWA 启动只加载当前页，事件和提醒列表默认 compact，详情页再展开证据，减少手机端等待和重复消息。
- 本地词典翻译辅助；PWA 不再提供翻译维护模块，后续优先接本地大模型自动翻译。
- 同一事件规则归并：最新流、搜索、详情、提醒和事件页展示同事件来源、证据和归并解释。

## 当前版本节点

- 当前用户可见版本：`1.6`。
- 版本升级必须同步更新 `docs/RELEASE_NOTES.md`、`docs/PRD.md`、`frontend/static/index.html`、`frontend/static/service-worker.js` 和相关测试。

## 当前实现边界

- 不支持登录态、Cookie、验证码、JS 渲染、浏览器自动化或反爬绕过来源。
- 不支持外部翻译 API、云模型、embedding、语义向量聚类、行情/资金/财务量化确认或自动交易。
- 事件归并仍是可审计规则 MVP，可能漏合并或误合并。
- 默认不自动删除情报 item；运行日志清理必须显式执行 dry-run/execute 脚本。

## 后续读取建议

- 默认排除：`data/`、`logs/`、`__pycache__/`、`docs/archive/`；仓库已提供 `.rgignore`。
- 查运行方式读 `README.md`。
- 查架构边界读 `docs/ARCHITECTURE.md`。
- 查产品需求读 `docs/PRD.md`。
- 查行业域评分规则读 `docs/INDUSTRY_DOMAIN_SELECTION.md`。
- 查开发与版本规则读 `docs/DEVELOPMENT.md` 和 `docs/RELEASE_NOTES.md`。
- `docs/archive/` 只保留历史摘要，除非追溯旧决策，不作为当前状态来源。
