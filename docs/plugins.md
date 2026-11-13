# EndBox 插件开发指南

> 对应实现：`endbox/plugins/`（宿主、API、存储、manifest），插件管理页 `endbox/ui/views/plugins.py`。
> 宿主契约见 `docs/CONTRACT.md §10`。本指南面向想给 EndBox 写插件的人。

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

| 目录 | 用途 | 管理页显示 |
| --- | --- | --- |
| `<EndBox 安装目录>/plugins/` | 随程序分发的示例 / 内置插件 | 来源「随程序分发」 |
| `%APPDATA%/EndBox/plugins/` | 用户自己安装的插件 | 来源「用户安装」 |

同一插件 id 只允许出现一次；重复 id 的后来者会被记为「加载失败」，不会覆盖先加载的插件。

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
  "min_app_version": "0.1.0",
  "permissions": ["timeline:read"]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 唯一 id，字母/数字/`_`/`.`/`-`，字母或数字开头，≤64 字符 |
| `name` | 否 | 显示名称，缺省用 id |
| `version` | 否 | 版本号字符串，默认 `0.0.0` |
| `author` | 否 | 作者 |
| `description` | 否 | 一句话说明，显示在管理页 |
| `icon` | 否 | 图标名（见 `endbox/ui/icons.py`，如 `clock`/`timeline`/`lock`/`spark`），未知时退回 `plugin` |
| `entry` | 否 | 入口文件，必须是插件目录内的相对路径，默认 `plugin.py` |
| `order` | 否 | 侧边栏排序，数字小的靠前 |
| `min_app_version` | 否 | 要求的最低 EndBox 版本；不满足时插件被标记为加载失败，主程序照常运行 |
| `permissions` | 否 | 权限数组，只认宿主白名单；未知权限被忽略并记录日志 |

**坏 manifest 不会影响主程序**：JSON 损坏、缺 `id`、`entry` 指向不存在的文件、版本不满足，
都会被记为「加载失败」并在插件管理页展示具体原因。

## 3. 入口文件

入口必须定义名为 `Plugin` 的类（建议继承 `EndBoxPlugin`），宿主用
`importlib.util.spec_from_file_location` 以 `endbox_plugin_<id>` 为模块名导入，
因此不会和主程序模块重名。

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
        # 插件加载时注册视图（宿主自动加 plugin_<id>_ 前缀）
        self.ctx.register_view(
            HelloView, sidebar_icon="spark", sidebar_label="你好", order=50
        )

    def on_enable(self):
        self.ctx.logger.info("hello enabled")

    def on_disable(self):
        # 停掉定时器、轮询等；之后宿主会卸载插件
        pass

    def on_unload(self):
        pass
```

> 入口里的 `Plugin` 类也可以不继承 `EndBoxPlugin`，但宿主会注入 `ctx` 属性；
> 推荐继承，拿到的 IDE 提示更完整。

入口按「包」加载，插件目录在模块的 `__path__` 里，所以可以拆分子文件并用相对导入：

```python
# plugin.py
from .helpers.data import load_seed   # 指向插件目录里的 helpers/data.py
```

宿主卸载时会一并清掉 `<模块名>.*` 子模块；子文件不需要在 `plugin.json` 里声明。

## 4. 生命周期

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
- 禁用状态记在设置 `plugins.disabled` 里，重启后保持禁用。

生命周期钩子、事件处理函数、插件导入过程中抛出的**任何**异常
（包括 `SystemExit`）都会被宿主捕获、写日志，并显示在插件管理页；不会冒泡到主程序。

## 5. PluginContext API

`PluginContext` 是插件能接触到的全部宿主能力。所有 UI 更新必须在主线程完成。

### 5.1 视图

```python
ctx.register_view(view_cls, sidebar_icon="spark", sidebar_label="计时", order=10)
```

- `view_cls` 必须是 `endbox.ui.views.base.BaseView` 的子类；
- 宿主**不修改**你传入的类，而是动态生成子类并设置：
  `key = "plugin_<插件id>_<原 key>"`、`section = "plugin"`；
- 因此插件视图会自动出现在侧边栏「插件」分区，并带独立分区标识；
- 同一个插件注册多个视图时，key 冲突会自动加 `_2` 后缀。

插件视图和其他视图一样遵守设计规则：深色纸面、细线分区、右下切角按钮、
黄色 `#FFFA00` 只做填充、无圆角/投影/发光/渐变、中文文案、数字必须真实。
可复用 `endbox.ui.kit` 的部件（`Panel / Label / CutButton / FieldText / InfoFlag / LaTeXView / confirm ...`）。

**界面文案**：所有用户可见文字都必须放在 `assets/text/zh-CN.json` 的 `plugins` section，
代码里用 `from endbox.core.text import t` 取值（`{name}` 占位符）；不要写死中文字面量。

**删除必须确认**：清空历史、删除条目、取消收藏等删除/清空动作必须先调
`from endbox.ui.kit import confirm`：

```python
if not confirm(self, title=..., message=..., action_text=...):  # danger=True 用于删除
    return
```

示例插件 `timers` 删除单个计时、清空已结束计时都已接确认框。

### 5.2 私有存储

```python
ctx.storage.get("timers", [])
ctx.storage.set("timers", timers)
ctx.storage.update({"last_seen": 123})
ctx.storage.delete("legacy_key")
ctx.storage.all()
```

- 落盘位置：`%APPDATA%/EndBox/plugin_data/<插件id>.json`，每个插件一份，互相不可见；
- 写入是「临时文件 + 原子替换」；磁盘错误只记录日志，不把异常抛回插件；
- 存储文件损坏时退回空字典（不会让插件加载失败）。

### 5.3 事件订阅（白名单）

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
事件处理函数抛异常只会记日志，不会影响发布方或其他订阅者。

### 5.4 只读快照（需要权限）

```python
snapshot = ctx.timeline_snapshot()   # 需要 timeline:read
account = ctx.account_snapshot()     # 需要 account:read
```

- 未在 manifest 声明对应权限 → 抛 `PermissionError`，消息里写明缺哪个权限、怎么授权，并写日志；
- 对应服务未载入（`ctx.services.timeline is None`）→ 返回 `{}`，插件应显示「— / 未载入」，**不要编造数据**；
- 服务内部读取失败 → 记录日志并返回 `{}`，不会把异常甩给插件；
- 返回的是快照副本，结构见 `docs/CONTRACT.md §7 / §8`。

### 5.5 其他

| 成员 | 用途 |
| --- | --- |
| `ctx.plugin_id` | 插件 id |
| `ctx.plugin_dir` | 插件目录（`pathlib.Path`） |
| `ctx.manifest` | 解析后的 `PluginManifest`（`permissions` 已过滤成白名单内） |
| `ctx.logger` | 插件专属 logger（`endbox.plugins.<id>`，写入 `%APPDATA%/EndBox/logs/endbox.log`） |
| `ctx.notify(text, kind)` | 全局 Toast，kind = `info` / `ok` / `warn` |

## 6. 权限与安全

| 权限 | 能力 |
| --- | --- |
| `timeline:read` | `ctx.timeline_snapshot()` |
| `account:read` | `ctx.account_snapshot()` |

- 插件**拿不到** `AppContext`、主窗口、服务实例、凭据存储和文件系统 API；
- 越权调用会被拒绝并写日志（日志里带插件 id），插件管理页也会显示该插件声明的权限；
- 插件代码仍然以主程序权限运行，所以只安装你信任的插件。

## 7. 错误隔离与调试

| 场景 | 表现 |
| --- | --- |
| 入口导入失败 / 语法错误 | 插件状态「加载失败」，管理页显示异常摘要（悬停看 traceback） |
| `on_load` / `on_enable` 崩溃 | 同上；加载过程中注册的视图自动回滚 |
| `on_disable` / `on_unload` 崩溃 | 只记录日志，继续卸载 |
| manifest 损坏 / 缺入口 / 版本不满足 | 跳过该插件并显示原因 |
| 循环引用 / 导入自身 | 按导入异常隔离，其他插件与主程序照常运行 |
| 越权调用 | `PermissionError` + 日志；插件界面可自行捕获展示 |
| 事件处理函数崩溃 | 日志记录，不影响其他订阅者 |

排错入口：

- 日志文件：`%APPDATA%/EndBox/logs/endbox.log`（按大小轮转）；
- 插件管理页（侧边栏「插件管理」）：状态、错误摘要、权限、加载耗时、视图列表，
  可启用/禁用、重新加载、打开插件目录、刷新扫描；
- 开发时用 `python tools/shot.py <视图key> shots/x.png dark` 直接渲染插件视图，
  视图 key 形如 `plugin_timers_timers`。

## 8. 示例插件

| 插件 | 展示的能力 |
| --- | --- |
| [`timers`](../plugins/timers/README.md) | 注册视图、私有存储持久化、插件级 `QTimer`、`ctx.notify` 到点提示 |
| [`timeline_stats`](../plugins/timeline_stats/README.md) | `timeline:read` 权限、读快照算真实统计、白名单事件订阅、服务缺失时降级 |
| [`permission_demo`](../plugins/permission_demo/README.md) | 无权限时越权被拒、把 `PermissionError` 原因显示在界面上、授权方式 |

> 依赖限制：只使用主程序已安装的依赖（PyQt6、httpx、Pillow、pywin32 可选、标准库）。
> 不要 `pip install` 新包；需要新增依赖请先和主程序维护者沟通。
