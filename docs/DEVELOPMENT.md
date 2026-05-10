# Signal Harbor 开发手册

## 版本升级规则

后续任何版本升级都必须同时完成以下三件事：

1. 更新版本升级说明书。
2. 升级项目版本号。
3. 确认前端左上角 Logo/品牌区展示的新版本号与文档一致。

版本升级说明书统一维护在 `docs/RELEASE_NOTES.md`。每次版本升级至少记录：

- 新版本号。
- 升级日期。
- 主要新增功能。
- 修复或调整内容。
- 对用户可见的变化。
- 已知未完成边界。
- 验证命令和结果摘要。

版本号采用数字版本，不使用日期版本。升级规则如下：

- 大更新：更新个位数，例如 `1.2` -> `2.0`。
- 中等更新：更新小数点后一位，例如 `1.2` -> `1.3`。
- 小版本更新：更新小数点后两位，例如 `1.2` -> `1.21`，再到 `1.22`。

当前前端展示位置：

- `frontend/static/index.html` 中的 `#appVersion`
- `frontend/static/index.html` 中的 `data-app-version`

版本升级时必须同步检查：

- `docs/PRD.md` 文档信息中的版本号。
- `frontend/static/index.html` 左上角展示版本号。
- `frontend/static/service-worker.js` 的 shell cache 名称，确保手机端不会继续使用旧静态资源。
- `tests/test_static_pwa.py` 中与版本号、cache 名称相关的断言。
- `docs/RELEASE_NOTES.md` 是否已新增对应版本说明。

若只做内部文档或测试清理、不影响用户可见功能，可以不升级版本号；但一旦用户需要对比新旧页面、手机端 PWA 展示变化、API 行为变化或数据源/分析链路变化，就必须升级版本号并补充版本升级说明。

## 验证要求

版本升级对应的最终验证至少包括：

```bash
python3 scripts/check.py
python3 -m compileall -q backend scripts
```

如修改 `frontend/static/app.js`，还应执行：

```bash
node --check frontend/static/app.js
```

最终回复或交接说明中必须粘贴 `unittest`、`compileall` 和前端语法检查的摘要。
