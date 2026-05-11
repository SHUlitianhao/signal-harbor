# 项目上下文摘要

本文件保留项目协作与迁移背景；日常开发请先读 `docs/CURRENT_STATE.md`。

## 当前定位

- 项目名称方向：本机自托管投资情报与行业域热度识别系统。
- 使用场景：单用户、本机常驻、Android 手机浏览器/PWA 远程查看。
- 产品目标：采集公开信息源，完成标准化、去重、分析、搜索、收藏、提醒、事件归并、行业域热度识别、关联监控股票池、精简降噪和手机端展示。
- 明确边界：不输出直接买卖建议，不接登录态、Cookie、验证码、JS 渲染或反爬绕过来源。

## 当前节点

- 手机远程访问闭环已验证可用。
- RSSHub 已作为外部上游服务接入，Signal Harbor 通过 `type=rsshub` 配置消费公开 RSSHub route。
- PWA 已具备行业域、最新流、事件、数据源、搜索、专题、收藏、提醒和运行页面。
- 当前用户可见版本为 `1.6`，版本升级规则见 `docs/DEVELOPMENT.md`。

## 当前文档

- `docs/CURRENT_STATE.md`：后续 agent 默认先读的短上下文。
- `README.md`：启动、手机访问、RSSHub、数据源和 API 摘要。
- `docs/PRD.md`：产品需求和总体边界。
- `docs/ARCHITECTURE.md`：架构、数据流和模块边界。
- `docs/ROADMAP.md`：阶段路线和已完成能力。
- `docs/DEVELOPMENT.md`：开发、测试和版本升级规则。
- `docs/RELEASE_NOTES.md`：版本升级说明。
- `docs/archive/`：历史交接和一次性反馈摘要，默认不作为当前状态读取。

## 协作方式

- 对话中的 AI 负责方案、拆解、评审与文档沉淀。
- Codex CLI 负责仓库内代码实现、验证和迭代修改。
- 后续迁移项目时，应保留 `AGENTS.md`、`docs/CURRENT_STATE.md` 和本文件。
- 检索时优先遵守 `.rgignore`，避免读取运行数据、缓存和归档历史。
