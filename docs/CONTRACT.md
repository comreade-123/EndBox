# EndBox 开发约定（所有模块共用）

> 主程序壳、设计系统、部件库、325 计算器、设置页、插件宿主接口已经就位。
> 这份文档是各模块与主程序之间的**唯一契约**。先读完再动手。

## 0. 运行与自检

```bash
python main.py                                   # 启动整个工具箱（Windows 桌面）
python tools/shot.py timeline shots/tl.png dark  # 渲染某个视图为 PNG（不弹窗）
python tools/shot.py timeline shots/tl.png light 1320 860
python -m compileall -q endbox                   # 语法检查
python run_tests.py tests/test_<module>.py -q    # 单个模块测试
python run_tests.py -q                          # 测试（pytest 已 vendor；各模块自建 tests/test_<module>.py）
```

- Python 3.11，Windows 11。只允许用已安装的依赖：`PyQt6`、`httpx`、`Pillow`、`pywin32`（可选）、标准库。
  **不要 pip install 新包**；二维码已 vendor 在 `endbox/thirdparty/qrcode`。
- 所有 UI 更新必须在主线程。网络/耗时操作走 `endbox.core.workers.run_async`。
- 界面文案用中文 + 宽体英文眉标；英文眉标是中文标题的直译（`// TIMELINE` / `技能轴`）。
- 数字必须真实（真实时间、真实计数）；没有数据就显示 `—` 或"未连接"，不编造。
- 每个模块必须能在**其他模块服务缺失**时正常打开（`ctx.services.xxx is None` 是合法状态）。

## 1. 设计系统（endfield-ui，必须遵守）

变体卡：**夜班**（黄 `#FFFA00` / 中性纸 / standard 切角 / 等高线底纹 / compact 密度 / calm 动效），默认深色。

> 用户指定偏差：**窗口外框 / 侧边栏面板 / 主显示面板用圆角**（参考鹰角官方启动器，整窗 16px、面板 12px），
> 这部分只属于壳层，由 `ui/main_window.py`、`ui/sidebar.py` 负责；**视图内部**仍然严格执行下面的规则
> —— 可点击元素切角、正文容器直角、圆角 0。

固定规则：

1. 只有一个强调色（`pal.signal`），只表示当前位置、可点击内容、醒目提醒，**只做填充**；黄底上写墨色。
2. 切掉一个角 = 可以按（一律右下角切，`paint.cut_rect(rect, cut, "br")`）；不可点的容器是直角；圆角一律 0。
3. 红色（`pal.danger`）**只给删除**。告警、失败、未连接用黄色小旗 + 墨线。
4. 细线分区（`pal.rule`，1px），不用投影、发光、装饰性渐变。
5. 数字用宽体等宽字体（`F.number_font` / `F.field_font`），编号日期计数都是真实数据。
6. 动效短、硬、机械：出现 `curve_at("snap", t)`、擦入/退出 `curve_at("mech", t)`，单次 280–360ms；
   关闭动效（设置 `app.animations=false`）时全部静止。
7. 键盘焦点必须可见（`paint.paint_focus`）。

## 2. 目录与文件归属

| 路径 | 归属 | 说明 |
| --- | --- | --- |
| `endbox/app.py`、`endbox/app_context.py`、`endbox/config.py`、`endbox/services.py` | Lead | 装配、上下文、设置存储 |
| `endbox/core/**` | Lead | 日志、事件总线、DPAPI、线程、HTTP |
| `endbox/theme/**` | Lead | tokens / 字体 / 绘制工具 / 动效 / QSS |
| `endbox/ui/kit/**`、`icons.py`、`glass.py`、`main_window.py`、`sidebar.py`、`topbar.py`、`branding.py` | Lead | 主壳与部件库 |
| `endbox/ui/views/base.py`、`__init__.py` | Lead | 视图基类与发现列表（**不要改**） |
| `endbox/ui/views/calc325.py`、`settings.py`、`endbox/modules/calc325/**` | Lead | 325 计算器、设置页 |
| `endbox/modules/account/**`、`endbox/ui/views/account.py` | 账号模块 | |
| `endbox/modules/timeline/**`、`endbox/ui/views/timeline.py` | 排轴模块 | |
| `endbox/modules/hud/**`、`endbox/ui/views/hud.py` | HUD 模块 | |
| `endbox/plugins/**`、`endbox/ui/views/plugins.py`、根目录 `plugins/**` | 插件模块 | |

只改自己范围内的文件。需要 lead 的基础设施改动 → 发消息说明需求，不要直接改。

## 3. 主题与绘制 API（`endbox.theme`）

```python
from endbox.theme.tokens import theme, Palette, CUT_SMALL, CUT_BUTTON, CUT_PANEL, MOTION
p = theme.p                # 当前主题调色板（浅/深各一套，切换时主题管理器会广播）
p.paper / p.panel / p.ink / p.ink2 / p.rule / p.chip / p.signal / p.on_signal
p.trace / p.tag_bg / p.tag_fg / p.danger / p.scrim / p.sidebar / p.topbar
p.content / p.panel_surface / p.glass / p.hatch / p.contour / p.is_dark
```

```python
from endbox.theme import paint, fonts as F
paint.cut_rect(rect, cut, "br")          # QPainterPath
paint.fill_cut(p, rect, color, cut, "br")
paint.stroke_cut(p, rect, color, cut, "br", width=1)
paint.paint_hatch(p, rect, ink, alpha=0.22)   # 45° 斜线纹
paint.paint_contours(p, rect, p.contour)      # 背景等高线（只用于大块空背景）
paint.paint_tri_strip(p, rect, p)             # 3px 三色条：只用于页面开始处/详情标题下
paint.paint_focus(p, rect, p.trace)
paint.draw_text(p, rect, text, font, color, align, elide=True)
paint.hairline(p, x1, y1, x2, y2, color)
paint.paint_focus(...)
F.font(14, weight=QFont.Weight.Bold, wide=True, cjk_first=True, letter_spacing=0)
F.eyebrow_font(11)   # 宽体大写小字
F.field_font(13)     # 字段数字
F.number_font(34)    # 大数字（tnum）
F.display_font(27)   # 大字标
```

动效：`from endbox.theme.motion import curve_at, Curve`；`curve_at("snap"|"mech"|"ease", t)`。

自定义部件在 `paintEvent` 里画；主题切换时主窗口会调用整棵控件树的 `refresh_theme()`（可选实现）与 `update()`。

## 4. 部件库（`from endbox.ui.kit import ...`）

- `Label(text, role, wrap=True)`：role = `eyebrow/field/title/h2/h1/body/muted/small/num/ink2/mono`。
- `SectionHead(eyebrow, title)`：`// EYEBROW` 在上、中文粗体在下。
- `Panel(title, eyebrow)`：细线面板；`.add(widget)` / `.add_layout(layout)` / `.add_header_action(widget)` / `.body`。
- `CutButton(text, variant, icon=None, checkable=False)`：variant = `default/primary/ghost/danger/chip`；`clicked`。
- `IconButton(icon, tooltip)`：30–32px 方形图标按钮。
- `Segmented(items)` + `.changed(int)`、`.index`、`.set_index(i, emit=False)`。
- `Toggle(text, checked=...)`：开关块。
- `FieldText(fields)`：`2026.11.13 │ 6,380` 字段行；`.set_fields([...])`。
- `Badge(text, signal=False)`：编号标签。`NumberStat(label, value, size=34, unit="")`。
- `Hairline(horizontal=True/False)`、`Spacer(w,h,expand)`。
- `ScrollArea()` + `set_content(widget)`。
- `EmptyState(title, description, action=widget)`、`HatchBand(height)`、`Avatar(size)`。
- `ProgressBar()` + `set_ratio(0..1)`。
- `Toast`、`ToastHost`（主窗口已有 `window.toast.show_message(text, kind)`；视图里用 `self.ctx.notify(text, "ok"|"warn"|"info")`）。
- `BusyBar()` + `start()/stop()`（处理中，斜线纹扫过）；`InfoFlag(text, kind="info"|"warn")`。
- `QRCanvas(size=208)` + `set_matrix(list[list[bool]])`。

图标名（`endbox.ui.icons.paint_icon(p, name, rect, color)`，也可 `IconButton(name)`）：
`account timeline hud calc settings plugin min max restore close sun moon refresh play pause stop plus minus trash copy link lock unlock search chevron-down/up/left/right arrow-right check clock battery wifi wifi-off user server grid list download external warning info zoom-in zoom-out save folder spark drag qr eye pin`。

## 5. 视图契约（`endbox/ui/views/base.py`）

```python
class XxxView(BaseView):
    key = "xxx"                 # 唯一，与侧边栏跳转使用
    eyebrow = "XXX"             # 顶栏英文眉标
    title = "中文标题"           # 顶栏与侧边栏显示
    icon = "timeline"           # 上面的图标名
    order = 20                  # 侧边栏顺序：home=5, account=10, timeline=20, hud=30, calc325=40, plugins=80, settings=90
    sidebar_label = "技能轴"     # 侧边栏短标签（2–4 个汉字）
    def build(self): ...        # 构建界面，self._root 是空 QVBoxLayout
    def on_show(self): ...      # 切到前台：开始播放/轮询
    def on_hide(self): ...      # 离开前台：停止轮询；后台服务不受影响
    def refresh(self): ...      # 数据变化后刷新
    def topbar_fields(self) -> list[str]   # 顶栏右侧真实字段
    def topbar_actions(self) -> list[QWidget]  # 顶栏右侧按钮（不要放窗口控制）
```

视图文件必须叫 `endbox/ui/views/<模块>.py`，类名固定（`AccountView`、`TimelineView`、`HudView`、`PluginManagerView`），
因为 `endbox/ui/views/__init__.py` 已经这样导入。视图内部可以随意拆分文件（放 `endbox/modules/<模块>/` 或同级私有模块）。

服务工厂：各模块在自己的包 `__init__.py` 里提供

```python
def create_service(ctx: AppContext) -> XxxService: ...
```

`endbox/app.py` 会 `import endbox.modules.<name>` 并调用它。视图通过 `self.ctx.services.<name>` 访问服务；
服务缺失时（`None`）要显示明确的"未载入"状态，不能崩。

## 5.1 首页 / 首次启动问候

`endbox/ui/views/home.py`（Lead 负责）是首次启动、还没点开任何工具时显示的一屏：

- 大问候「{时段问候}，{名字}」+「今天肝点什么？」+ 真实日期 / 时间 / 星期；
- 名字来自 `ctx.services.account.state["nickname"]`，未绑定 / 演示数据 / 服务缺失时显示「管理员」；
- 时段规则与文案在 `endbox/core/greeting.py`（有单测，改文案只改这里）；
- 模块入口行显示各模块真实状态，点击 `ctx.open_view(key)`；底部为真实统计与快捷操作；
- **每次启动都进入 `home`**（用户要求）：不恢复上次的模块；切到任意工具仍记录 `last_view` 供调试/记录。

## 6. 共享上下文

```python
self.ctx.settings      # config.Settings，点路径读写：get("hud.scale", 1.0) / set("hud.scale", 1.2)
self.ctx.bus           # 事件总线：emit(topic, **payload)；subscribe(topic, handler(**payload))
self.ctx.services      # account / timeline / hud / plugins 四个服务（可能为 None）
self.ctx.notify(text, kind)   # 全局 Toast
self.ctx.open_view(key)       # 跳转到另一个模块
self.ctx.main_window          # 主窗口（需要挂全局快捷键/覆盖层时用）
```

事件主题（`endbox.core.event_bus.Topics`）：

| 主题 | 发布方 | 载荷 |
| --- | --- | --- |
| `account.state` | 账号服务 | `nickname, online, avatar_path, provider, roles` |
| `account.updated` | 账号服务 | `snapshot: dict`（见 §7） |
| `timeline.state` | 排轴服务 | `state: dict`（见 §8） |
| `timeline.tick` | 排轴服务 | `t, playing, active, duration` |
| `timeline.changed` | 排轴视图 | 无 |
| `hud.state` | HUD 服务 | `enabled: bool` |
| `ui.toast` | 任意 | `text, kind` |

## 7. 账号模块契约

**服务** `endbox.modules.account.AccountService`

```python
class AccountService(QObject):
    state: dict                    # 见下
    def refresh(self) -> None      # 异步拉取；完成后 emit account.state / account.updated
    def snapshot(self) -> dict     # 只读快照（供插件白名单使用）
    def logout(self) -> None
    def stop(self) -> None
```

`state` / `snapshot` 结构（缺失字段用 None）：

```python
{
  "online": bool,
  "provider": "skland" | "bilibili" | "",
  "nickname": str | None,
  "uid_masked": str | None,
  "avatar_path": str | None,
  "roles": [{"name": str, "level": int | None, "server": str | None,
             "class": str | None, "element": str | None}],
  "last_sync": int,          # unix 秒；0 = 从未同步
  "demo": bool,              # 演示数据必须显式标注
  "error": str | None,
}
```

- 官服：森空岛 Token（用户从 `https://web-api.skland.com/account/info/hg` 的 `data.content` 复制）。
  已记录但未验证的接口：`https://binding-api-account-prod.hypergryph.com/account/binding/v1/binding_list`。
  接口失败必须给出人话错误 + 保留 Token 供重试，不能吞异常。
- B 服：`https://passport.bilibili.com/x/passport-login/web/qrcode/generate` 拿 `url + qrcode_key`，
  用 `endbox.thirdparty.qrcode.main.QRCode` 生成矩阵 → `QRCanvas.set_matrix()`；
  每 2s 轮询 `.../web/qrcode/poll?qrcode_key=...`（状态码 86101 未扫码 / 86090 已扫待确认 / 0 成功）。
  成功后 Cookie 存 `endbox.core.secure_store.secure_store`（key 自己定，如 `bili.sessdata`）。
- 凭据只能经 `secure_store.set(key, value)` 落盘（DPAPI）。
- 所有网络调用走 `endbox.core.net`（get_json/post_json，异常 `NetworkError`），线程走 `workers.run_async`。
- 扫码轮询停止条件：视图 on_hide、成功、失败、取消。
- 界面至少包含：连接状态（在线/未连接/演示）、官服 token 输入卡、B 服扫码卡、玩家信息卡（头像/昵称/UID/角色列表）、
  同步时间字段、未连接时的空状态、演示数据开关（黄色小旗标注"演示数据，非真实账号"）、退出登录（danger，仅此一处红色）。

## 8. 排轴模块契约

**服务** `endbox.modules.timeline.TimelineService`

```python
class TimelineService(QObject):
    # 数据模型（可在服务里持有，视图只渲染）
    def play(self) -> None
    def pause(self) -> None
    def seek(self, t: float) -> None
    def stop(self) -> None
    def snapshot(self) -> dict          # 见下（HUD/插件只读）
    def set_team(self, names: list[str]) -> None
```

播放时钟由**服务**持有（视图切走时 HUD 仍要收到 tick）。播放中每 ~50ms `bus.emit(timeline.tick, ...)`；
状态变化（增删事件、队伍变化、播放/暂停）emit `timeline.state`。

`snapshot()`：

```python
{
  "t": float, "playing": bool, "duration": float,
  "team": [{"name":"伊冯","class":"术士","element":"灼热",
            "avatar": r"D:\...\素材\operators\伊冯\avatar.png"}],
  "active": [{"operator":"伊冯","kind":"skill","remaining":2.4}],
  "events": [{"operator": int, "kind": "skill"|"link"|"ult", "start": float, "duration": float}],
  "buffs": [{"name":"攻击 +20%","start": float, "end": float}],
}
```

素材读取：`endbox.config.GAME_ASSETS`（指向 `素材/`）。干员清单 `operators/roster.json`（id→名字），
每个干员目录有 `avatar.png / link.png / skill.png / ult.png / class.txt / element.txt`；
`classes/`、`elements/` 是职业与属性图标。**这些素材是游戏素材，只在本地工具里使用，不重新分发。**

界面要求（原生拖拽时间轴）：

- 顶部：4 个队伍槽（从干员池选择，可换人），显示头像/职业/属性。
- 工具栏：添加技能 / 连携 / 终结技、播放/暂停、回到起点、缩放 ±、吸附开关、清空、保存。
- 时间轴：时间刻度尺（秒，真实刻度）、每个干员一条轨道；技能块可**拖拽**移动、右边缘拖拽改时长、
  Delete 删除、点击选中（黄色描边）；吸附到 0.1s；Ctrl+滚轮缩放；播放头随时间移动。
- Buff 覆盖轨：可选，显示区间与覆盖率百分比（真实计算）。
- 状态/统计字段：总时长、事件数、可见 Buff 覆盖率。
- 数据持久化：`%APPDATA%/EndBox/timeline.json`（用 `ctx.settings` 不合适，服务自己读写文件，容错）。

## 9. HUD 模块契约

**服务** `endbox.modules.hud.HudService`

```python
class HudService(QObject):
    enabled: bool
    def set_enabled(self, on: bool) -> None
    def show(self) -> None
    def hide(self) -> None
    def toggle(self) -> None
    def stop(self) -> None
```

- 覆盖层：无边框、置顶、不抢焦点、鼠标穿透（`endbox.ui.glass.set_click_through` + Qt 的
  `WindowTransparentForInput | WindowStaysOnTopHint | Tool | FramelessWindowHint`），`WA_TranslucentBackground`。
- 视觉：参考 `zmd-charge-main/Views/HudWindow.axaml` 的"灵动岛"胶囊（560×60，圆角在终末地风格里改用**切角**，
  仍保持工业感）：顶部居中出现→撑开显示状态→收回；波纹用同心方框/刻度线，不用发光。
- 内容（都是真实数据）：排轴播放状态（当前时间 / 播放中 / 当前释放的技能与干员）、账号昵称与在线状态、
  电池百分比（`GetSystemPowerStatus`，ctypes）。
- 全局热键 `Ctrl+Alt+H`：`RegisterHotKey` + `QAbstractNativeEventFilter`（有失败分支，日志 + 界面提示）。
- 控制视图提供：开关、预览、位置（顶部居中/靠左/靠右）、缩放 0.6–1.4、透明度、显示项开关、热键字段（重新注册）。
- 关闭 HUD 时释放窗口与热键；重复 show/hide 不能泄漏窗口。

## 10. 插件模块契约

**宿主** `endbox/plugins/host.py::PluginHost(ctx, registry)`

- 扫描两个目录：根目录 `plugins/`（随程序分发的示例插件）和 `%APPDATA%/EndBox/plugins/`（用户安装）。
  每个插件一个目录，含 `plugin.json` + 入口 `.py`。
- `plugin.json`：

```json
{
  "id": "timers", "name": "材料刷新计时", "version": "0.1.0", "author": "EndBox",
  "description": "记录采集点的刷新倒计时", "icon": "clock", "entry": "plugin.py",
  "order": 10, "min_app_version": "0.1.0", "permissions": ["timeline:read"]
}
```

- 生命周期：`on_load()` `on_enable()` `on_disable()` `on_unload()`（都可选）；插件崩溃/导入失败必须被捕获，
  记录日志并在插件管理页显示错误，绝不影响主程序。
- 插件 API（`endbox/plugins/api.py`）：

```python
from endbox.plugins.api import EndBoxPlugin, PluginContext
class MyPlugin(EndBoxPlugin):
    def on_load(self):
        self.ctx.register_view(MyView, sidebar_icon="spark", sidebar_label="计时", order=10)
```

`PluginContext` 提供：`plugin_id / plugin_dir / manifest / logger / storage`（私有 JSON 持久化字典）、
`event_bus.subscribe(topic, handler)`（只允许白名单主题：`timeline.*`、`account.updated`、`ui.theme_changed`）、
`register_view(view_cls, sidebar_icon, sidebar_label, order)`、`account_snapshot()`（需 `account:read`）、
`timeline_snapshot()`（需 `timeline:read`）。越权访问抛 `PermissionError` 并记录日志。
插件注册的视图 `section = "plugin"`（宿主自动设置），在侧边栏"插件"分区显示并有区分标识。

- 插件管理视图 `PluginManagerView`（key=`plugins`，eyebrow=`PLUGINS`，title=`插件`，icon=`plugin`，order=80，
  sidebar_label=`插件管理`）：列出插件（名称/版本/作者/状态/权限/错误）、启用/禁用、重新加载、打开插件目录、
  侧边栏区分标识说明、空状态。`section` 用默认 `core`（它属于内置视图）。
- 至少 3 个示例插件，必须真的能用：
  1. `timers`：多个倒计时（增删、持久化、结束提示）；
  2. `timeline_stats`：读 `timeline:read`，显示排轴事件统计（真实数字）；
  3. 一个展示错误隔离/权限的最小插件（如无权限时调用 `timeline_snapshot` 被拒绝并在界面显示原因）。

## 11. 毛玻璃与自动化

- 主窗口毛玻璃是**自绘**的（抓桌面 → 模糊 → 纸色染色，见 `endbox/ui/glass.py`）；
  Windows 原生 acrylic 在该系统版本只做平涂，不作为主实现。
- 截图 / 自动化测试设 `ENDBOX_NO_GLASS=1` 关闭毛玻璃，得到稳定背景：
  `tools/shot.py`、`tools/integration_check.py`、`tests/conftest.py` 已默认设置。
- 数据目录默认是**软件同目录的 `data/`**（便携模式，用户要求），程序目录不可写时退回 `%APPDATA%\EndBox`；
  旧 `%APPDATA%\EndBox` 的数据会在首次启动自动迁移；`ENDBOX_DATA_DIR` 可覆盖（沙箱 / 测试用）。

## 12. 验收清单（各模块自查）

- [ ] `python tools/shot.py <你的视图> shots/<name>.png dark` 与 `light` 都能出图，不报错。
- [ ] 切到其他视图再切回来，状态不丢；离开视图后后台轮询/播放停止（HUD 除外：它订阅后台服务）。
- [ ] 服务缺失（`ctx.services.xxx is None`）时视图能打开并显示"模块未载入"。
- [ ] 网络失败、空数据、权限拒绝都有明确界面反馈；没有伪造数据。
- [ ] 黄色只用于填充；红色只用于删除；没有圆角、投影、发光、渐变。
- [ ] 所有数字真实；空状态是一句完整的话。
- [ ] 键盘可聚焦、焦点可见；HUD 重复开关不泄漏窗口。
- [ ] 没有修改自己范围外的文件；新增依赖 0 个。


## 13. 文案 / 确认框 / LaTeX（2026-11 新增约定）

### 13.1 全部界面文案进一个文件
- 唯一文件：`assets/text/zh-CN.json`（UTF-8，JSON 对象，点分 key，`{name}` 占位符）。
- 代码：`from endbox.core.text import t`，例如 `t("home.actions.start_timeline")`。
- 只改自己模块的 section（home/account/timeline/hud/calc325/plugins/settings/shell/common/app）；
  用 edit 局部修改，遇 `FS_STALE_VERSION` 先重读再改；不要整文件覆盖别人的 section。
- 代码里除 docstring / 注释 / 日志外不得保留中文字面量；`tests/test_foundation.py::test_text_catalog_keys_exist`
  会扫描静态 `t("...")` 的 key 是否存在。
- 运行时改完 JSON：设置 → 数据与凭据 →「重新加载文案」（类属性/导入时求值的标签需重启）。

### 13.2 删除 / 清空 / 重置必须确认
```python
from endbox.ui.kit import confirm
if not confirm(self, title="清空技能轴", message="清空后无法恢复，确定吗？", action_text="清空"):
    return
```
`danger=True`（默认）时确认按钮为红色，只用于删除/清空类操作；重置类用 `danger=False`。
默认焦点在「取消」，Esc 取消。所有删除入口（步骤、轴库、编队、历史、收藏、退出登录、清空插件计时……）都要走它。

### 13.3 LaTeX 公式渲染
```python
from endbox.ui.kit import LaTeXView, render_latex
view.set_latex(r"rac{172+3}{5} 	imes 4 = 325", text="(172+3)/5 × 4 = 325")
```
- matplotlib mathtext 渲染（vendored `vendor/`，失败自动退回文本）；支持 `rac \int \sum \lim \sqrt`、上下标、
  `\left( 
ight)`；**不支持 `egin{matrix}`**，矩阵请写成展开式。
- 325 计算器在等级/种子/生成/历史恢复后实时重渲染。

### 13.4 排轴按键逻辑（）
- Q+1~4 战技、E+1~4 连携、Alt+1~4 终结（按住修饰再按数字）；长按 1~4 = 终结（纯数字 token 忽略）；
  重击格只提示不按键（实机重击设定）；F8 退出 / F9 回第一段 / F6-F7 透明度 / F10 反复开关 / F11 回反复起点。
- 重击不产生按键步骤；注释/换行/分段/反复标记运行时跳过。
- 参考实现 为 GPL-3.0：只对齐行为，不复制源码/文案；EndBox 保持自己的实现与许可证。
- 输入服务 API 见 `docs/input.md`；`tools/verify_runtime_os.py` 做「token→引擎」全链路验证。

### 13.5 便携数据目录
- 默认 `软件目录/data/`；`ENDBOX_DATA_DIR` 覆盖；旧 `%APPDATA%\EndBox` 首次启动自动迁移；
  程序目录不可写时回退 `%APPDATA%\EndBox`。
