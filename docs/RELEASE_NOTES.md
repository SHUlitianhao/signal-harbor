# Signal Harbor 版本升级说明书

本文件用于记录每次用户可见版本升级。后续版本升级时必须先补充本文件，再同步更新前端展示版本号和相关测试。

## 当前版本：1.3

- 前端展示位置：`frontend/static/index.html` 左上角 Logo/品牌区。
- 当前阶段能力：本机自托管公开源采集、RSSHub 适配、手机远程 PWA、数据源管理、翻译辅助、同事件归并、事件视图和阶段性节点清理。
- 后续升级要求：按 `docs/DEVELOPMENT.md` 的版本升级规则追加新版本条目。

### v1.3 - 2026-05-10

- 升级类型：中等更新。
- 主要新增：手机端新增 RSS/RSSHub 来源可进入公开源采集；事件归并逻辑从存储层拆出为可审计 helper；新增当前状态短文档。
- 修复调整：SQLite 连接增加 busy timeout 和 WAL 尝试；JSON/HTML 未配置 mapping 时在数据源页标注为目录说明；过期交接与翻译反馈文档归档。
- 用户可见变化：左上角版本号升级为 `1.3`，PWA 数据源页显示来源是否可采集。
- 已知边界：数据清理默认只预览，真实删除必须显式执行；事件归并仍为规则 MVP；翻译维护 API 保留为内部维护能力。
- 验证结果：
  - `python3 scripts/check.py`：通过，49 个 unittest 用例全部 OK。
  - `python3 -m compileall -q backend scripts`：通过。
  - `node --check frontend/static/app.js`：通过。

## 版本条目模板

### vX.Y / vX.YZ - YYYY-MM-DD

- 升级类型：大更新 / 中等更新 / 小版本更新
- 主要新增：
- 修复调整：
- 用户可见变化：
- 已知边界：
- 验证结果：
  - `python3 scripts/check.py`：
  - `python3 -m compileall -q backend scripts`：
  - `node --check frontend/static/app.js`：
