# EndBox 插件开发与插件市场

> 对应实现：`endbox/plugins/`（宿主、API、清单、存储、市场）、插件管理页
> `endbox/ui/views/plugins.py`、官方市场 `market/`、打包脚本 `tools/pack_plugin.py`。
> 宿主契约见 `docs/CONTRACT.md §10`。本指南面向两类人：想给 EndBox 写插件的人，
> 以及想发布 / 维护插件市场条目的人。

## 1. 插件长什么样

每个插件是一个独立目录，包含 `plugin.json`（清单）+ 入口 `.py`（建议同时放一份 `README.md`）：

```
plugins/
  timers/
    plugin.json
    plugin.py
    README.md
```

插件放在两个位置，宿主启动时都会扫描：

| 目录 | 用途 | 管理页来源 |
| --- | --- | --- |
| `<EndBox 安装目录>/plugins/` | 随程序分发的示例 / 内置插件 | 来源「随程序分发」 |
| `<EndBox 数据目录>/plugins/` | 用户安装的插件（市场安装 / 手动放置） | 来源「用户安装」 |

同一插件 id 只允许出现一次；重复 id 的后来者会被记为「加载失败」，不会覆盖先加载的插件。
所以**随程序分发的插件不要试图用市场条目覆盖**：市场页对这类插件显示「等程序更新」。

## 2. plugin.json

```json
{
  "id": "timers",
  "name": "材料刷新计时",
  "version": "0.1.0",
  "author": "EndBox",
  "description": "记录采集点的刷新倒计时",
  "icon": "clock",
  "entry": "plugin.py",
  "order": 10,
  "min_app_version": "1.1.0",
  "permissions": ["timeline:read"]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 唯一 id，字母/数字/`_`/`.`/`-`，字母或数字开头，≤64 字符 |
| `name` | 否 | 显示名称，缺省用 id |
| `version` | 否 | 语义化版本号字符串，默认 `0.0.0`；市场更新比较用它 |
| `author` | 否 | 作者 |
| `description` | 否 | 一句话说明，显示在管理页 |
| `icon` | 否 | 图标名（见 `endbox/ui/icons.py`），未知时退回 `plugin` |
| `entry` | 否 | 入口文件，必须是插件目录内的相对路径，默认 `plugin.py` |
| `order` | 否 | 侧边栏排序，数字小的靠前 |
| `min_app_version` | 否 | 要求的最低 EndBox 版本；不满足时插件被标记为加载失败，主程序照常运行 |
| `permissions` | 否 | 权限数组，只认宿主白名单（见 §6）；未知权限被忽略并记录日志 |

**坏 manifest 不会影响主程序**：JSON 损坏、缺 `id`、`entry` 指向不存在的文件、版本不满足，
都会被记为「加载失败」并在插件管理页展示具体原因。

## 3. 入口文件与生命周期

入口必须定义名为 `Plugin` 的类（建议继承 `EndBoxPlugin`），宿主用
`importlib.util.spec_from_file_location` 以 `endbox_plugin_<id>` 为模块名导入，
不会和主程序模块重名。入口按「包」加载，可以用相对导入拆分子文件：

```python
# plugin.py
from endbox.plugins.api import EndBoxPlugin
from endbox.ui.views.base import BaseView
from endbox.ui.kit import Label, Panel


class HelloView(BaseView):
    key = "hello"
    eyebrow = "HELLO"
    title = "你好"
    icon = "spark"
    sidebar_label = "你好"

    def build(self):
        panel = Panel("来自插件的问候", "HELLO")
        panel.add(Label("这个视图是插件注册的。", "muted", wrap=True))
        self._root.addWidget(panel)


class Plugin(EndBoxPlugin):
    def on_load(self):
        self.ctx.register_view(HelloView, sidebar_icon="spark", sidebar_label="你好", order=50)

    def on_enable(self):
        self.ctx.logger.info("hello enabled")

    def on_disable(self):
        # 停掉定时器、轮询等；之后宿主会卸载插件
        pass

    def on_unload(self):
        pass
```

宿主按以下顺序调用（全部可选）：

```
导入模块 → Plugin(ctx) → on_load() → on_enable()
```

| 时机 | 调用 | 说明 |
| --- | --- | --- |
| 加载 | `on_load()` | 注册视图、恢复数据、建立订阅。抛异常 = 该插件加载失败并回滚（已注册的视图会撤销） |
| 启用 | `on_enable()` | 首次加载时在 `on_load` 之后调用 |
| 禁用 | `on_disable()` → `on_unload()` | 禁用是**完整卸载**：取消事件订阅、移除已注册视图、丢弃模块 |
| 重新加载 / 刷新扫描 / 退出 | `on_unload()` | 清理资源；异常只记录日志 |

- 「启用」= 重新导入并执行 `on_load` + `on_enable`；
- 「禁用」/「重新加载」都不会让已经弹出的界面残留：侧边栏的插件视图会立即移除；
- 禁用状态记在设置 `plugins.disabled` 里，重启后保持禁用；
- 市场安装 / 更新插件后宿主会**热重载**（先禁用旧版本、换文件、再启用），不需要重启程序。

生命周期钩子、事件处理函数、插件导入过程中抛出的**任何**异常
（包括 `SystemExit`）都会被宿主捕获、写日志，并显示在插件管理页；不会冒泡到主程序。

## 4. PluginContext API

`PluginContext` 是插件能接触到的全部宿主能力。所有 UI 更新必须在主线程完成。

### 4.1 视图

```python
ctx.register_view(view_cls, sidebar_icon="spark", sidebar_label="计时", order=10)
```

- `view_cls` 必须是 `endbox.ui.views.base.BaseView` 的子类；
- 宿主**不修改**你传入的类，而是动态生成子类并设置
  `key = "plugin_<插件id>_<原 key>"`、`section = "plugin"`；
- 因此插件视图会自动出现在侧边栏「插件」分区，并带独立分区标识；
- 同一个插件注册多个视图时，key 冲突会自动加 `_2` 后缀。

插件视图和其他视图一样遵守设计规则：深色纸面、细线分区、右下切角按钮、
黄色 `#FFFA00` 只做填充、无圆角/投影/发光/渐变、中文文案、数字必须真实。
可复用 `endbox.ui.kit` 的部件（`Panel / Label / CutButton / FieldText / InfoFlag / confirm ...`）。

**界面文案**：所有用户可见文字都必须放在 `assets/text/zh-CN.json` 的 `plugins` section，
代码里用 `from endbox.core.text import t` 取值（`{name}` 占位符）；不要写死中文字面量。

**删除必须确认**：删除 / 清空 / 卸载动作必须先调
`from endbox.ui.kit import confirm`：

```python
if not confirm(self, title=..., message=..., action_text=...):  # danger=True 用于删除
    return
```

示例插件 `timers` 删除单个计时、清空已结束计时，`link_notes` 删除记录都已接确认框。

### 4.2 私有存储

```python
ctx.storage.get("timers", [])
ctx.storage.set("timers", timers)
ctx.storage.update({"last_seen": 123})
ctx.storage.delete("legacy_key")
ctx.storage.all()
```

- 落盘位置：`<EndBox 数据目录>/plugin_data/<插件id>.json`，每个插件一份，互相不可见；
- 写入是「临时文件 + 原子替换」；磁盘错误只记录日志，不把异常抛回插件；
- 存储文件损坏时退回空字典（不会让插件加载失败）。
- 卸载插件会删除插件目录，但**不会**自动删除私有数据；需要的话请自行在 `on_unload` 里清理。

### 4.3 事件订阅（白名单）

```python
sub = ctx.event_bus.subscribe("timeline.state", handler)   # handler(**payload)
sub.cancel()
```

允许的主题只有：

| 匹配方式 | 主题 |
| --- | --- |
| 精确 | `account.updated` |
| 精确 | `ui.theme_changed` |
| 前缀 | `timeline.*`（如 `timeline.state`、`timeline.tick`、`timeline.changed`） |

订阅其他主题会抛 `PermissionError` 并记录日志。插件卸载时宿主自动取消全部订阅。

## 5. 只读快照（需要权限）

```python
snapshot = ctx.timeline_snapshot()   # 需要 timeline:read
snapshot = ctx.account_snapshot()    # 需要 account:read
```

- 服务未载入时返回空字典 `{}`（插件应显示「模块未载入」而不是报错）；
- 未申请对应权限时抛 `PermissionError`，宿主把拒绝原因写进日志，插件应捕获并显示；
- 快照结构见 `docs/CONTRACT.md §7 / §8`；不要修改返回值，也不要把它写回宿主。

## 6. 权限表

| 权限 | 授予的能力 | 数据范围 |
| --- | --- | --- |
| `timeline:read` | `ctx.timeline_snapshot()` | 排轴只读快照（时间、编队、事件、Buff） |
| `account:read` | `ctx.account_snapshot()` | 账号只读快照（昵称、UID 脱敏、角色列表） |

- 白名单定义在 `endbox/plugins/manifest.py::ALLOWED_PERMISSIONS`；
- 未在白名单里的权限会被忽略（管理页显示「已忽略权限」），不会报错；
- 权限只授予**只读**能力：插件始终拿不到 AppContext、服务对象、主窗口、凭据与网络客户端；
- 插件自己发起网络请求请自行负责（宿主不提供 HTTP 客户端），并且必须在 UI 线程之外执行。

## 7. 插件市场

### 7.1 索引格式（schema v1）

官方源是随程序分发的 `market/index.json`（打包时进 `_MEIPASS`，离线可用）：

```json
{
  "schema": 1,
  "name": "EndBox 官方插件市场",
  "updated": "2026-11-14",
  "repo": "https://github.com/comreade-123/EndBox",
  "plugins": [
    {
      "id": "link_notes",
      "name": "连携备忘录",
      "version": "0.1.0",
      "author": "EndBox",
      "description": "记录干员 / 组合的连携触发条件……",
      "icon": "link",
      "category": "排轴",
      "tags": ["排轴", "连携", "备忘录"],
      "homepage": "https://github.com/comreade-123/EndBox/tree/main/market/plugins/link_notes",
      "download_url": "packages/link_notes-0.1.0.zip",
      "sha256": "d8163e239bcdd4fab54c953e49c4464d0d67912301b62b19878ad82b04a1e78a",
      "size": 3409,
      "min_app_version": "1.1.0",
      "permissions": [],
      "changelog": "记录干员 / 组合的连携触发条件，保存在插件私有数据里。"
    }
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `schema` | 是 | 固定 `1`；不支持其他版本（解析器会拒绝） |
| `plugins[].id` | 是 | 插件 id，规则同 `plugin.json` |
| `plugins[].version` | 是 | 语义化版本，必须与包内 `plugin.json` 完全一致 |
| `plugins[].download_url` | 是 | 官方源允许 http(s) 或仓库内相对路径（相对 `market/`）；第三方源只允许 http(s) |
| `plugins[].sha256` | 是 | 64 位小写十六进制；安装前逐个校验，不匹配就取消 |
| `plugins[].size` | 否 | 包大小（字节），用于展示与下载上限预检 |
| `plugins[].min_app_version` | 否 | 与终点 `plugin.json` 的字段一致，市场页会提示「需要更高版本」 |
| `plugins[].permissions` | 否 | 只展示白名单内权限；未知权限被丢弃 |
| `plugins[].category/tags/homepage/changelog` | 否 | 展示与筛选用（分类、标签、主页、更新说明） |

解析规则：坏条目跳过并写日志，不影响同一索引里的其他插件；同一来源里重复 id 只保留第一条；
多个来源之间同 id 时，按「官方源优先，其次添加顺序」去重。

### 7.2 来源

- **官方源**：`market/index.json`，随程序分发，不需要网络即可浏览与离线安装
  （条目用仓库内相对路径，如 `packages/xxx.zip`）；
- **第三方源**：设置页 / 插件市场页里「插件源 → 添加源」填 http(s) 索引地址，
  存在设置 `plugins.market_sources`（JSON 列表）。添加时校验 URL 与索引 JSON（schema v1），
  坏源只在市场页显示一条「源加载失败」，不会影响官方源。
- 第三方索引拉取有 TTL（6 小时）缓存；离线时自动退回上次缓存并标注「离线缓存」。

### 7.3 安装 / 更新 / 卸载

市场页操作全部在后台线程执行，UI 显示下载进度条与错误提示；破坏性操作都走 `confirm()`：

- **安装**：下载 zip → 校验大小上限 → 校验 sha256 → 安全解压到临时目录 →
  校验 `plugin.json`（id 必须与条目一致、版本一致、`min_app_version` 满足）→
  换入 `<数据目录>/plugins/<id>/` → 宿主加载；
- **更新**：同上；替换前先在 UI 线程禁用旧插件，失败会回滚旧目录并重新启用；
- **卸载**：先在 UI 线程摘掉插件记录与视图，再删除插件目录；只允许卸载用户插件，
  随程序分发的插件市场页不提供卸载；
- **来源记录**：安装成功后写入 `<数据目录>/market_state.json`（`market` / 本地手动放置记为 `local`），
  已安装页会显示来源；市场页按它判断「已安装 / 可更新 / 等程序更新」。

### 7.4 安全模型（重要）

市场模块（`endbox/plugins/market.py`）**绝不执行**市场里的任何代码：

- 只做下载、校验、解压、清单校验、落盘；真正 import 插件仍是宿主既有加载器的职责；
- zip slip 防护：拒绝绝对路径、盘符、`..`、反斜杠穿越、符号链接、加密条目；
- 只接受「单个顶层插件目录」的包结构，且目录名必须是合法插件 id；
- 三层大小上限：索引文件 / 下载包 / 解压后总字节与文件数量（防 zip bomb）；
- sha256 必须匹配，否则删除文件并取消安装；
- 版本 / id / 最低宿主版本三重校验，避免「索引写 A、包里是 B」；
- 第三方源由用户自行添加，添加时只校验 http(s) 与 JSON 结构；请只添加可信来源。

## 8. 打包与发布

### 8.1 目录约定

```
market/
  index.json               官方市场索引（入库）
  packages/<id>-<version>.zip   官方插件包（入库，单顶层目录）
  plugins/<id>/...         只在市场分发（不随程序预装）的官方插件源码，如 link_notes
```

### 8.2 打包脚本

```bash
# 打包 plugins/ 与 market/plugins/ 下全部插件，重算 sha256 并更新索引
python tools/pack_plugin.py --all

# 只打一个插件
python tools/pack_plugin.py --plugin plugins/timers

# 校验索引里的 sha256 / size 与包文件是否一致
python tools/pack_plugin.py --all --check

# 发布到 GitHub raw 时把下载地址改成绝对 URL
python tools/pack_plugin.py --all --base-url https://raw.githubusercontent.com/comreade-123/EndBox/main/market
```

- 包结构固定为 `<id>/plugin.json`、`<id>/plugin.py` ……；
- 打包是**确定性**的（固定时间戳、排序文件），同样的源码重复打包 sha256 不变；
- 自动排除 `__pycache__`、`*.pyc`、隐藏文件与临时文件；
- `--all` 每次都会顺便执行 `--check`，确认索引与包一致。

### 8.3 发布流程

1. 改插件源码与 `plugin.json` 里的 `version`；
2. `python tools/pack_plugin.py --all`；
3. 跑 `python run_tests.py tests/test_market.py -q`（会校验官方索引与包一致）；
4. 提交 `market/index.json`、`market/packages/*.zip` 与插件源码；
5. 如果是放在 GitHub raw 的第三方源，用 `--base-url` 重新生成 `download_url`。

> 官方源条目默认使用仓库内相对路径，因此**离线也能安装**；用户用第三方源分发时
> 把 `download_url` 写成 https 地址即可。

## 9. 测试

```bash
python run_tests.py tests/test_plugins.py -q   # 宿主：加载隔离、生命周期、权限、存储
python run_tests.py tests/test_market.py -q    # 市场：索引、缓存降级、sha256、zip slip、安装/卸载
python run_tests.py -q                         # 全量
python tools/shot.py plugins shots/plugins_market_dark.png dark
```

`tests/test_market.py` 全部离线（`httpx.MockTransport`），覆盖：索引解析与坏条目、
官方相对路径安装、sha256 不匹配、zip slip / 绝对路径 / 符号链接 / 单目录 / 大小上限、
离线缓存降级、来源增删持久化、安装 / 覆盖 / 卸载目录结果，以及仓库官方索引与包的一致性。

## 10. 已知限制

- 插件市场只发布**打包好的 zip**；不会在安装时执行插件代码，也没有自动更新（点「更新」才更新）；
- 第三方源没有中心化审核，sha256 只能防传输损坏 / 替换，不能证明作者可信；
- 随程序分发的插件升级必须跟版本发布走，市场页只提示「等程序更新」；
- 插件权限只有只读快照两项（`timeline:read` / `account:read`），暂不支持写操作；
- 依赖 PyQt6 的插件必须与主程序使用同一 Qt 大版本（当前 PyQt6）。
