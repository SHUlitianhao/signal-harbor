# Signal Harbor 版本升级说明书

本文件用于记录每次用户可见版本升级。后续版本升级时必须先补充本文件，再同步更新前端展示版本号和相关测试。

## 当前版本：1.6

- 前端展示位置：`frontend/static/index.html` 左上角 Logo/品牌区。
- 当前阶段能力：本机自托管公开源采集、RSSHub 适配、手机远程 PWA、数据源管理、翻译辅助、同事件归并、行业域热度识别、关联监控股票池 Top 10、列表精简和阶段性节点清理。
- 后续升级要求：按 `docs/DEVELOPMENT.md` 的版本升级规则追加新版本条目。

### v1.6 - 2026-05-11

- 升级类型：中等更新。
- 主要新增：行业域推荐改为“短期强催化 + 持续热点”混合评分，新增短期催化分、持续热点分、噪声扣分和推荐理由。
- 修复调整：PWA 启动和刷新改为当前页优先；事件、提醒和行业域列表默认精简返回，大量相关报道和证据改为详情页查看。
- 用户可见变化：页面点击等待减少，提醒列表减少重复刷屏，行业域首页默认只展示更集中的 Top 5。
- 已知边界：仍为规则评分，不接行情、资金、财务、回测或外部模型；compact 列表会隐藏部分证据，详情页保留更多证据。
- 验证结果：
  - `python3 scripts/check.py`：通过，60 个 unittest 用例全部 OK。
  - `python3 -m compileall -q backend scripts`：通过。
  - `node --check frontend/static/app.js`：通过。

### v1.5 - 2026-05-11

- 升级类型：中等更新。
- 主要新增：行业域详情增加“关联监控股票池 Top 10”，从可审计股票池配置和近期新闻/事件证据计算关联度、排序、选择原因和证据链接。
- 修复调整：行业域榜单增加监控样本数量摘要；详情页展示行情、资金、财务和超额收益验证的“未接入/待验证”状态，避免把新闻热度伪装为交易结论。
- 用户可见变化：左上角版本号升级为 `1.5`，行业域详情可查看每个方向的监控样本股票、关联理由、命中标签、相关事件和原始证据。
- 已知边界：股票池不是交易列表；仍未接入行情、资金、财务、估值、回测或券商接口，超额收益验证仅预留字段。
- 验证结果：
  - `python3 scripts/check.py`：通过，58 个 unittest 用例全部 OK。
  - `python3 -m compileall -q backend scripts`：通过。
  - `node --check frontend/static/app.js`：通过。

### v1.4 - 2026-05-11

- 升级类型：中等更新。
- 主要新增：行业域热度识别第一期，基于新闻/事件、来源质量、标签、实体、风险词和证据链生成 A 股行业域热门榜。
- 修复调整：PWA 默认第一屏切换为“行业域”；新增行业域评分配置、只读 API、手机端分数拆解和证据详情。
- 用户可见变化：左上角版本号升级为 `1.4`，PWA 主导航第一项为“行业域”，行业域详情展示研究优先级、行情确认状态和原始证据链接。
- 已知边界：第一期不接行情、资金、财务、一致预期、估值、回测、个股筛选或自动交易；`market_confirmation` 明确显示为“未接入”。
- 验证结果：
  - `python3 scripts/check.py`：通过，55 个 unittest 用例全部 OK。
  - `python3 -m compileall -q backend scripts`：通过。
  - `node --check frontend/static/app.js`：通过。

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
