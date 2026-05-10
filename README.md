# Signal Harbor

Signal Harbor 是一个本机自托管的投资与国际资讯情报收集器。它把公开来源内容采集到本地 SQLite，完成标准化、去重、摘要、评分、搜索、收藏、提醒、同事件归并，并通过手机 PWA 查看。系统只提供研究辅助信息和证据引用，不提供直接买卖建议。

当前用户可见版本：`1.3`。后续版本升级必须同步更新 `docs/RELEASE_NOTES.md`、`docs/PRD.md`、左上角版本号、service worker 缓存名和相关测试。

## 当前能力

- 公开源采集：fixture、RSS、RSSHub、JSON、低风险静态 HTML。
- RSSHub 作为外部上游服务接入；Signal Harbor 只消费公开 RSS，不并入 RSSHub 的 Node/TypeScript 依赖。
- 首批 RSSHub 来源：华尔街见闻重要快讯、财联社加红电报、金十重要快讯、同花顺重要要闻、DW 中文国际新闻。
- 数据源页支持新增、启用和停用来源；手机端新增 RSS/RSSHub 可进入公开源采集，JSON/HTML 缺少 mapping 时仅作为目录说明。
- 本地词典翻译辅助；PWA 已移除翻译维护模块，词典状态和词典维护 API 仅作为 internal maintenance 保留。
- 同一事件规则归并：最新流、搜索、详情、提醒和事件页展示来源数、相关报道、证据链接和归并解释。
- 手机远程可用：本机服务 + Basic Auth + 用户自行配置的安全通道，例如 Tailscale Serve。
- 默认不自动删除情报 item；运行日志清理必须显式执行脚本。

当前不支持登录态、Cookie、验证码、JS 渲染、浏览器自动化、反爬绕过、外部翻译 API、云模型、embedding、自动交易或多用户 SaaS。

## 本地启动

先运行检查：

```bash
python3 scripts/check.py
```

导入 fixture 或公开源：

```bash
python3 scripts/ingest_fixture.py
python3 scripts/ingest_public_sources.py
```

启动本机服务：

```bash
python3 scripts/run_dev.py
```

启动时采集一次公开源：

```bash
SIGNAL_HARBOR_INGEST_ON_STARTUP=true python3 scripts/run_dev.py
```

后台定时采集公开源，单位为分钟，`0` 表示关闭：

```bash
SIGNAL_HARBOR_INGEST_INTERVAL_MINUTES=30 python3 scripts/run_dev.py
```

桌面浏览器访问：

```text
http://127.0.0.1:8765
```

## 手机访问

### 局域网模式

电脑和手机在同一可信局域网时，可让服务监听局域网地址：

```bash
SIGNAL_HARBOR_HOST=0.0.0.0 python3 scripts/run_dev.py
```

手机访问：

```text
http://<电脑局域网 IP>:8765
```

不要把该端口直接暴露到公网。

### Tailscale Serve HTTPS 模式

1. Windows 电脑和 Android 手机都安装 Tailscale，并登录同一个账号。
2. 在 WSL/Linux 项目目录启动 Signal Harbor：

```bash
cd <Signal Harbor 项目目录>
cp config/sources.local.example.json config/sources.local.json

SIGNAL_HARBOR_REMOTE_ACCESS=true \
SIGNAL_HARBOR_REMOTE_USERNAME='<你的用户名>' \
SIGNAL_HARBOR_REMOTE_PASSWORD='<你的密码>' \
SIGNAL_HARBOR_HOST=0.0.0.0 \
SIGNAL_HARBOR_SOURCES_CONFIG=config/sources.local.json \
python3 scripts/run_dev.py
```

3. 在 Windows PowerShell 中配置 Tailscale Serve：

```powershell
tailscale serve --bg --https=443 http://127.0.0.1:8765
tailscale serve status
```

`tailscale serve status` 会显示类似 `https://<电脑名>.<tailnet>.ts.net` 的地址。手机保持 Tailscale 已连接，用浏览器访问该 HTTPS 地址并输入 Basic Auth 用户名和密码。

电脑重启后通常不需要重新配置 Tailscale Serve，但仍需重新启动 Signal Harbor，除非后续另行配置系统服务或开机自启。

## 手机 PWA 工作流

- “最新”查看最新情报，支持刷新、来源筛选、翻译状态筛选、收藏和加入专题。
- “事件”查看同一事件的多来源报道，每条保留原始 URL 作为证据。
- “数据源”管理来源；RSS/RSSHub 可采集，JSON/HTML 需要 mapping 后才采集。
- “搜索”支持关键词、来源、主题、分数、收藏、翻译状态和保存搜索。
- “专题”管理观察清单、专题集合和收藏沉淀。
- “提醒”展示站内消息、来源、时间、分数、摘要/译文、标签、风险词和详情入口。
- “运行”展示健康状态、远程访问状态、公开源调度器、最近 TaskRun，并可手动触发公开源采集。
- service worker 只缓存静态 shell，不缓存 `/api/` 数据。

## 配置

默认配置参考 `config/app.example.json`。常用环境变量：

- `SIGNAL_HARBOR_CONFIG`：应用配置路径。
- `SIGNAL_HARBOR_DATA_DIR`：本地数据目录。
- `SIGNAL_HARBOR_SOURCES_CONFIG`：公开源配置路径。
- `SIGNAL_HARBOR_TRANSLATION_CONFIG`：翻译配置路径。
- `SIGNAL_HARBOR_INGEST_ON_STARTUP`：启动后是否采集一次公开源。
- `SIGNAL_HARBOR_INGEST_INTERVAL_MINUTES`：后台采集间隔。
- `SIGNAL_HARBOR_HOST` / `SIGNAL_HARBOR_PORT`：HTTP 监听地址和端口。
- `SIGNAL_HARBOR_REMOTE_ACCESS`：是否启用远程访问保护。
- `SIGNAL_HARBOR_REMOTE_USERNAME` / `SIGNAL_HARBOR_REMOTE_PASSWORD`：Basic Auth 凭据，密码只能来自环境变量或私密配置。
- `SIGNAL_HARBOR_REMOTE_PUBLIC_BASE_URL`：运行页展示用远程入口，不包含密码、token 或 Cookie。

默认数据写入 `data/signal_harbor.sqlite3`，该目录不进入 Git。不要在代码或文档中硬编码本机绝对路径、真实账号、Cookie、token 或隧道地址。

`config/sources.local.json` 是个人本机来源配置，默认被 Git 忽略。新环境需要真实 RSSHub/国内来源时，先复制 `config/sources.local.example.json`，按需把来源改为 `enabled=true`，再通过 `SIGNAL_HARBOR_SOURCES_CONFIG=config/sources.local.json` 启动。

## RSSHub

RSSHub 是独立外部服务。推荐本机 Docker 常驻：

```bash
cd ../RSSHub
docker compose up -d
curl http://127.0.0.1:1200/healthz
```

Signal Harbor 通过 `type="rsshub"`、`rsshub_base_url`、`rsshub_route`、`rsshub_healthcheck_path`、`rsshub_check_health` 和 `rsshub_instance_name` 消费 RSSHub route。启动真实来源时建议使用：

```bash
cp config/sources.local.example.json config/sources.local.json
SIGNAL_HARBOR_SOURCES_CONFIG=config/sources.local.json python3 scripts/run_dev.py
```

实际接入只限公开、无需登录、无需 Cookie、无需验证码、无需 JS 渲染、无需规避反爬的 route。若 route 返回 401、403、429 或明显阻止自动访问，应停用该 route。

## 数据保留与清理

当前默认不自动删除 `items`、`task_runs`、`notifications` 或证据 URL。同事件归并只影响展示，不删除原始情报。

清理脚本默认 dry-run，只统计将清理的旧 `task_runs` 和 `notifications`：

```bash
python3 scripts/cleanup_data.py --database data/signal_harbor.sqlite3 --days 90
```

显式执行才会删除运行日志类数据：

```bash
python3 scripts/cleanup_data.py --database data/signal_harbor.sqlite3 --days 90 --execute
```

脚本不删除情报 item。执行前建议备份数据库。

## API 摘要

主要 API：

- 运行：`GET /api/health`、`GET /api/runtime/status`、`POST /api/tasks/ingest-public`
- 来源：`GET /api/sources`、`POST /api/sources`、`POST /api/sources/{id}/toggle`
- 情报：`GET /api/items/latest`、`GET /api/items/search`、`GET /api/items/{id}`、`POST /api/items/{id}/translate`
- 事件：`GET /api/events`、`GET /api/events/{event_key}`
- 研究沉淀：favorites、collections、watchlists、saved-searches、alert-rules
- 提醒：`GET /api/notifications`
- 内部翻译维护：`GET /api/translation/status`、`GET/POST /api/translation/glossary`、`POST /api/items/translate-batch`

`GET /api/sources` 会返回 `collectable`、`collectability_status` 和 `collectability_label`，用于 PWA 区分可采集来源、目录说明和需要 mapping 的来源。API 详细字段以 `docs/ARCHITECTURE.md` 为准。

## 文档入口

- `docs/CURRENT_STATE.md`：后续 agent 默认先读的短上下文。
- `docs/ARCHITECTURE.md`：架构、数据流、API 和边界。
- `docs/PRD.md`：产品需求。
- `docs/ROADMAP.md`：阶段路线和下一步。
- `docs/DEVELOPMENT.md`：开发、测试和版本规则。
- `docs/RELEASE_NOTES.md`：版本升级说明。
