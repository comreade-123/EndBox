# 版本与更新

> 对应实现：`endbox/version.py`（唯一版本源）、`endbox/modules/update/`（检查逻辑）、
> `endbox/ui/views/settings.py`（「版本与更新」区）、根目录 `version.json` 与 `CHANGELOG.md`。
> 任务板：task-4。

## 1. 版本号从哪里来

| 位置 | 作用 | 谁改 |
| --- | --- | --- |
| `endbox/version.py::__version__` | **程序自身版本**：关于页、设置页、插件 `min_app_version` 判断、请求 UA 都用它 | 发布时手改 |
| `version.json`（仓库根目录） | **线上最新版本声明**：版本号、发布说明、下载地址；供「检查更新」兜底源使用 | 发布时手改 |
| `CHANGELOG.md` | 面向玩家的更新记录；GitHub release notes 也建议从这里摘 | 每次发版 |
| `tools/version_info.txt` | Windows 文件属性里的版本（PyInstaller `version=`） | 发版时与 `version.py` 同步 |

`endbox/__init__.py` 只是 `from endbox.version import __version__` 的转发，不要在别处再写死版本号。
`ENDBOX_VERSION` 环境变量可以临时覆盖「当前版本」显示（开发/打包验证用，合法语义化版本才生效）。

**为什么不从 `version.json` 反推当前版本**：仓库里的 `version.json` 描述的是「线上最新版」。
源码已经升级、`version.json` 还没改的时候，如果拿它当本地版本，程序会把自己当成旧版本，
更新检查就永远显示「有新版本」。所以本地版本只认 `version.py`。

## 2. 检查更新逻辑

```
GitHub Releases API（发布后自动生效）
   ├─ 成功 → 取最新正式版（跳过 draft / 优先非 prerelease）
   └─ 失败（无网 / 429 限流 / 404 / SSL）→ version.json（raw，轻量兜底）
          ├─ 成功 → 标记 degraded（来源 version.json）
          └─ 失败 → userdata/cache/update_check.json（上次成功结果，标记 cached）
                 └─ 也没有 → ok=False + 一句人话错误（静默，不弹系统框）
```

- 请求封装在 `endbox/modules/update/net.py`：超时 8s、识别 403/429 限流、区分
  `offline / timeout / ssl / http / not_found / bad_json` 等错误类型；
- 正式环境按正常 SSL 校验；开发机有 HTTPS 中间人代理时设 `ENDBOX_INSECURE_TLS=1`
  才会跳过证书校验（会写警告日志，不做静默降级）；
- 检查永不抛异常：`UpdateService.check()` 返回 `CheckResult`；
- 自动检查 6 小时内复用缓存（`check(force=False)`），设置页按钮走 `check(force=True)`；
- 忽略版本存在设置 `update.ignored_version` 里；忽略只针对该版本，更高版本仍会提示；
- 所有网络请求都在 `workers.run_async` 的后台线程里，不阻塞 UI。

## 3. 界面

设置页「版本与更新」区（`Update` 面板）：

- 当前版本 / 最新版本 / 上次检查时间 / 来源；
- 「检查更新」（异步，BusyBar 扫过）、「打开发布页」（打开浏览器）、
  「忽略此版本 / 取消忽略」；
- 更新日志正文来自 release notes 或 `version.json` 的 `notes`；
- 失败显示 `InfoFlag`（黄色小旗，不用红色）；没有网络只是提示，不影响使用。

主壳顶栏的低干扰提示由 Lead 接线（`endbox/modules/update/badge.py::UpdateHint`）：
有更新且未忽略时显示一个小黄条「有新版本 vX」，点击进设置页；不弹窗、不抢焦点。

## 4. version.json 格式（schema 1）

```json
{
  "schema": 1,
  "version": "1.1.0",
  "released": "2026-11-14",
  "title": "v1.1.0 · 插件市场与版本更新",
  "min_version": "1.0.0",
  "notes": ["用数组或一段字符串都可以"],
  "downloads": {
    "exe": "https://github.com/comreade-123/EndBox/releases/download/v1.1.0/EndBox.exe",
    "zip": "https://github.com/comreade-123/EndBox/releases/download/v1.1.0/EndBox-v1.1.0.zip"
  },
  "html_url": "https://github.com/comreade-123/EndBox/releases/tag/v1.1.0",
  "changelog_url": "https://github.com/comreade-123/EndBox/blob/main/CHANGELOG.md"
}
```

下载声明也兼容 `download_url` 字符串或 `assets` 数组（见 `feed.parse_version_payload`）。
缺下载地址时按钮会退回发布页。

## 5. 发布流程

1. 改 `endbox/version.py` 的 `__version__`（例如 `1.1.0`）；
2. 改 `version.json` 的 `version / released / title / notes / downloads / html_url`；
3. 在 `CHANGELOG.md` 顶部加一节 `## [1.1.0] - YYYY-MM-DD`；
4. 同步 `tools/version_info.txt`（`filevers/prodvers/FileVersion/ProductVersion`）；
5. 本地自检：

   ```bash
   python -m compileall -q endbox
   python run_tests.py -q
   python -m PyInstaller --clean --noconfirm EndBox.spec
   ```

6. 提交并打 tag：

   ```bash
   git add -A
   git commit -m "release: v1.1.0"
   git tag v1.1.0
   git push origin main --tags
   ```

7. GitHub Actions（`.github/workflows/release.yml`）会在 tag 推送后跑测试、打包
   `dist/EndBox.exe`、压缩 zip 并创建 Release（附件名 `EndBox.exe` / `EndBox-v1.1.0.zip`）；
8. 发布后不用再改代码：程序优先读 GitHub Releases，读不到再读 `version.json`。

> 当前仓库还没有 release/tag。没有发布时检查会落到「更新地址不存在 / 还没有 Release」
> 的提示，这是预期行为；`version.json` 已经指向 v1.1.0 的发布地址，发布后自动生效。

## 6. 测试与截图

```bash
python run_tests.py tests/test_update.py -q   # 解析 / 比较 / 降级 / 忽略版本，全部离线
python run_tests.py -q                        # 全量
python tools/shot.py settings shots/settings_update_dark.png dark
```

`test_foundation.py::test_text_catalog_keys_exist` 会扫描 `endbox/**/*.py` 里所有静态
`t("...")`，新增文案必须同步进 `assets/text/zh-CN.json` 的 `update` section。

## 7. 已知限制

- 检查更新只**检查**，不在程序内静默下载/执行安装包；下载由用户点击后在浏览器确认，
  避免自更新链路被中间人替换。
- GitHub API 无 token 时限流 60 次/小时；触发限流时会自动改用 `version.json`。
- 本机若强制 HTTPS 中间人，需要在开发环境设 `ENDBOX_INSECURE_TLS=1`，正式用户不需要。
