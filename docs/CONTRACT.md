# EndBox 开发约定（所有模块共用）

> 版本 1.1.0。主程序壳、设计系统、部件库、325 计算器、设置页、插件宿主与插件市场已经就位。
> 这份文档是各模块与主程序之间的**唯一契约**。先读完再动手。

## 0. 运行与自检

```bash
python main.py                                    # 启动整个工具箱（Windows 桌面）
python tools/shot.py timeline shots/tl.png dark   # 渲染某个视图为 PNG（不弹窗）
python tools/shot.py timeline shots/tl.png light 1320 860
python -m compileall -q endbox                    # 语法检查
python run_tests.py tests/test_<module>.py -q     # 单个模块测试
python run_tests.py -q                            # 全部测试（pytest 已 vendor）
python tools/integration_check.py                 # 装配完整应用，跑遍所有视图与深浅主题
python tools/verify_runtime_os.py                 # 排轴运行态按键链路验证
python tools/smoke_exe.py                         # 打包产物冒烟：启动 exe、检查服务/插件装配
python tools/fetch_game_data.py --validate-only   # 校验 assets/data 数据完整性
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
| `endbox/app.py`、`app_context.py`、`config.py`、`services.py`、`version.py` | Lead | 装配、上下文、设置存储、版本源 |
| `endbox/core/**` | Lead | 日志、事件总线、加密存储、线程、HTTP、问候、文案、输入 |
| `endbox/theme/**` | Lead | tokens / 字体 / 绘制工具 / 动效 / QSS |
| `endbox/ui/kit/**`、`icons.py`、`glass.py`、`main_window.py`、`sidebar.py`、`topbar.py`、`branding.py` | Lead | 主壳与部件库 |
| `endbox/ui/views/base.py`、`__init__.py`、`home.py` | Lead | 视图基类、发现列表、首页 |
| `endbox/ui/views/calc325.py`、`settings.py`、`endbox/modules/calc325/**` | Lead | 325 计算器、设置页 |
| `endbox/modules/update/**`、`version.json`、`CHANGELOG.md`、`.github/workflows` | Lead | 版本与更新 |
| `endbox/modules/account/**`、`endbox/ui/views/account.py` | 账号模块 | |
| `endbox/modules/timeline/**`、`endbox/ui/views/timeline.py` | 排轴模块 | |
| `endbox/modules/hud/**`、`endbox/ui/views/hud.py` | HUD 模块 | |
| `endbox/plugins/**`（含 `market.py`）、`endbox/ui/views/plugins.py`、根 `plugins/**`、`market/**` | 插件模块 | |
| `assets/data/**`、`tools/fetch_game_data.py`、`tools/sync_game_data.py`、`assets/gamedata/`（本地） | 数据 | |

只改自己范围内的文件。需要 Lead 的基础设施改动 → 发消息说明需求，不要直接改。

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
- `ProgressBar()` + `set_ratio(0..1)`、`BusyBar()` + `.start()/.stop()`、`InfoFlag(text, kind="info"|"warn")`。
- `QRCanvas(size=208)` + `set_matrix(list[list[bool]])`。
- `confirm(parent, title, message, action_text, danger=True)`：删除/清空/重置必须走它。
- `Toast` / `ToastHost`：视图里用 `self.ctx.notify(text, "ok"|"warn"|"info")`。

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
    def topbar_fields(self) -> list[str]        # 顶栏右侧真实字段
    def topbar_actions(self) -> list[QWidget]   # 顶栏右侧按钮（不要放窗口控制）
```

视图文件必须叫 `endbox/ui/views/<模块>.py`，类名固定（`AccountView`、`TimelineView`、`HudView`、`PluginManagerView`），
因为 `endbox/ui/views/__init__.py` 已经这样导入。视图内部可以随意拆分文件（放 `endbox/modules/<模块>/` 或同级私有模块）。

服务工厂：各模块在自己的包 `__init__.py` 里提供

```python
def create_service(ctx: AppContext) -> XxxService: ...
```

`endbox/app.py` 会 `import endbox.modules.<name>` 并调用它。视图通过 `self.ctx.services.<name>` 访问服务；
服务缺失时（`None`）要显示明确的"没启动"状态，不能崩。当前装配的服务：`account / timeline / hud / update / plugins`。

## 5.1 首页 / 首次启动问候

`endbox/ui/views/home.py`（Lead 负责）是每次启动先显示的一屏：

- 大问候「{时段问候}，{名字}」+「今天肝点什么？」+ 真实日期 / 时间 / 星期；
- 名字来自账号服务 `state["nickname"]`，未绑定 / 演示数据 / 服务缺失时显示「管理员」；
- 时段规则与文案在 `endbox/core/greeting.py`（有单测）；
- 模块入口行显示各模块真实状态，点击 `ctx.open_view(key)`；底部为真实统计与快捷操作；
- **每次启动都进入 `home`**（用户要求），不恢复上次的模块。

## 6. 共享上下文

```python
self.ctx.settings      # config.Settings，点路径读写：get("hud.scale", 1.0) / set("hud.scale", 1.2)
self.ctx.bus           # 事件总线：emit(topic, **payload)；subscribe(topic, handler(**payload))
self.ctx.services      # account / timeline / hud / update / plugins（可能为 None）
self.ctx.notify(text, kind)   # 全局 Toast
self.ctx.open_view(key)       # 跳转到另一个模块（主窗口已接成 show_view）
self.ctx.main_window          # 主窗口（需要挂全局快捷键/覆盖层时用）
```

事件主题（`endbox.core.event_bus.Topics`）：

| 主题 | 发布方 | 载荷 |
| --- | --- | --- |
| `account.state` | 账号服务 | `nickname, online, avatar_path, provider, roles` |
| `account.updated` | 账号服务 | `snapshot: dict`（见 §7） |
| `timeline.state` | 排轴服务 | `state: dict`（见 §8） |
| `timeline.changed` | 排轴视图 | 无 |
| `hud.state` | HUD 服务 | `enabled: bool` |
| `ui.theme_changed` | 主题管理 | `mode: "dark"|"light"` |
| `ui.toast` | 任意 | `text, kind` |

**排轴运行态与 HUD 互不订阅**：HUD 只读账号与系统数据，排轴运行提示由排轴自己的运行条显示（见 §8.1）。

## 7. 账号模块契约

**服务** `endbox.modules.account.AccountService`

```python
class AccountService(QObject):
    state: dict                          # 见下
    def refresh(self) -> None            # 异步拉取玩家资料
    def snapshot(self) -> dict           # 只读快照（插件 account:read 用）
    def select_role(self, index: int)    # 多角色切换
    def skland_scan_start(self, ...) / skland_scan_poll(...) / skland_scan_confirm(...)
    def skland_sign_attendance(self, ...)
    def bili_generate(...) / bili_poll(...) / commit_bili_login(...)
    def set_demo_mode(self, enabled: bool)
    def logout(self) / def stop(self)
```

`state` / `snapshot` 结构（缺失字段为 `None`）：

```python
{
  "online": bool,
  "provider": "skland" | "bilibili" | "",
  "nickname": str | None,            # 玩家昵称（演示数据时为演示名）
  "uid_masked": str | None,
  "avatar_path": str | None,         # 本地缓存头像
  "roles": [{"name","level","server","class","element","role_id","server_id"}],
  "role_index": int,
  "player": {                        # card/detail 解析结果；读不到为 None
     "role_id","server_id","server_name","nickname","level","world_level","exp",
     "avatar_url","avatar_path","char_num","weapon_num","doc_num",
     "create_time","last_login_time","save_time","main_mission",
     "cur_stamina","max_stamina","stamina_full_ts",   # 理智
     "chars": [{"name","char_id","rarity","rarity_key","profession","element",
                "level","evolve_phase","potential_level","weapon_name",
                "weapon_rarity","weapon_level","avatar_url","avatar_path"}],
     "domains": [{"name","level","settlements","collections"}],
     "bp": {"level","max","daily_cur","daily_max","achieve"},
  },
  "last_sync": int,                  # unix 秒；0 = 从未同步
  "demo": bool,                      # 演示数据必须显式标注
  "error": str | None,
  "flags": {...},                    # 界面用的派生状态（未连接/演示/过期…）
}
```

- 官服：森空岛 App 扫码 → OAuth token → grant → cred/token（HMAC 签名 V2）；之后
  `GET /api/v1/game/endfield/card/detail?roleId=&serverId=` 读完整玩家数据，
  带 query 的请求要把 query string 一起参与签名；header 需要 `sk-game-role: 3_{roleId}_{serverId}`。
  `/web/v1/game/endfield/enums` 可读枚举。接口是社区第三方接口，随时可能变。
- B 服：`passport.bilibili.com` 扫码（86101 未扫 / 86090 已扫待确认 / 0 成功），只提供 B 站身份，
  终末地角色数据仍要森空岛扫码。
- 凭据只经 `endbox.core.secure_store`（Windows DPAPI）落盘；日志不落明文；UID 脱敏。
- 网络走 `endbox.core.net`（异常 `NetworkError`），线程走 `workers.run_async`；轮询在 `on_hide` 停止。
- 签到接口是社区记录、未验证：失败只提示，不阻塞其他功能。
- 界面：连接状态带、扫码卡（官服 / B 服）、玩家卡、理智卡（当前/上限/满溢倒计时）、规模统计、
  干员列表（可排序筛选）、地区探索、通行证与每日任务、签到入口、退出登录（唯一红色入口）。
  演示模式必须显式标注；缺失字段显示 `—`。

## 8. 排轴模块契约

**服务** `endbox.modules.timeline.TimelineService`

数据模型是**有序步骤序列**（不是时间轴轨道）：`model.TimelineModel` 持有 4 人队伍与 `steps[]`。
步骤类型 `kind`：`attack`（普攻/重击提示）、`skill`（战技）、`link`（连携技）、`ult`（终结技）、
`switch`（切换干员）、`comment`、`break`、`section`、`repeat_start`、`repeat_end`。
`Step` 字段：`kind, slot(0-3), operator, skill(技能名/id), duration(预计秒), text(注释), count(反复次数)`。

**真实键位**（对齐游戏内设置，来源 MaaEnd 键位表 + 社区核对）：

| 动作 | 按键 |
| --- | --- |
| 普攻 | 鼠标左键（排轴只提示，不按键） |
| 战技 | `1` `2` `3` `4` 短按（对应 4 个干员槽位）；消耗 1 格技力，全队 3 格共享 |
| 终结技 | 长按 `1` `2` `3` `4` |
| 连携技 | `E`（全队共用，各角色有触发条件） |
| 切换干员 | `F1` `F2` `F3` `F4`（不是数字键） |
| 重击 | 普攻连段末段（长按鼠标左键），没有独立键盘键；排轴只提示 |

旧版把 `Q+n` 当战技、`E+n` 当连携、`Alt+n` 当终结，**已全部迁移**：读档/分享码时
`E+n → E`、`Q+n / ALT+n → 默认数字键`，重击字段原样保留。分享码格式为 v3（兼容读 v1/v2，导出统一 v3）。

```python
class TimelineService(QObject):
    def play(self) / pause(self) / stop(self)
    def snapshot(self) -> dict      # 排轴插件只读：team / steps / stats / running
    def set_team(self, names: list[str])
    def total_duration(self) -> float   # 按步骤 duration 求和
    def energy_cost(self) -> int        # 战技与终结计 1 格
```

界面要求：

- 队伍区：4 个槽位（干员池来自 `assets/data/operators.json`，34 名干员 / 180 条技能；
  素材优先读 `素材/`，缺失回退 `assets/gamedata/`）；快捷编队、编队库、清空队伍。
- 干员详情：职业 / 属性 / 稀有度 / 阵营 / 词缀 / 简介，技能名与图标来自游戏数据。
- 动作面板：战技 `1~4`、连携 `E`、终结 `长按 1~4`、切换 `F1~F4`、重击 `左键`；
  点按钮插入到选中步骤之后，未选中则追加到末尾。
- 序列区：步骤卡片、注释/换行/分段/反复、Ctrl 多选、←/→ 移动、Delete 删除、双击编辑耗时与技能名。
- 统计：总步骤、分段、反复、预计总耗时、技力消耗、队伍人数。
- 轴库 / 编队库 / 分享码（v3）/ 保存 / 导入导出。

### 8.1 运行提示条（关键：与 HUD 完全独立）

- **运行条是排轴模块自己的窗口**（`modules/timeline/run_overlay.py`），HUD 覆盖层
  （`modules/hud/**`）不显示排轴、不订阅排轴事件、不复用运行条窗口。两者互不调用。
- **默认整轴反复**：`RunEngine(repeat_all=True)` 跑完最后一步回到开头继续，永不结束；
  只有按 F8（或关掉整轴反复后跑到结尾）才结束。F10 临时切整轴反复。
- 提示条显示排轴自身信息：当前步骤、下一步按键、第 N 段、进度、反复圈数；
  卡片主视觉是**技能图标**（战技 / 连携 / 终结 / 重击用 `素材/gen` 里按干员生成的卡片图，
  切换干员画双向箭头），干员头像缩成左上角小徽标；**显示长度可调**（`card_count` 3–9，默认 5，含当前格）。
- **按对才前进；按错直接跳过这一步**（不弹提示）：被跳过的卡片标红 + 左右震动，
  前进到下一步时新当前卡片会出现白色圆圈从外向内收缩一次。
  运行状态里有 `wrong_seq` / `advance_seq` / `wrong_gindex` 供提示条做这类动效。
- 运行控制键：`F8` 退出、`F9` 回第一段、`F6/F7` 透明度、`F10` 整轴反复开关、`F11` 回反复起点。
- 分享对话框导出时附一段可直接粘贴的文案（干员列表 + 分享码），由
  `share.share_blurb(team, code)` 生成，模板在文案 JSON 的 `timeline.share.blurb_template`。
- 游戏以管理员运行时需同权限启动（UIPI），界面有提示。
- 输入服务 API 见 `docs/input.md`；`tools/verify_runtime_os.py` 做「按键 → 引擎」全链路验证。

## 9. HUD 模块契约

**服务** `endbox.modules.hud.HudService`

```python
class HudService(QObject):
    enabled: bool
    def set_enabled(self, on: bool) / show(self) / hide(self) / toggle(self)
    def stop(self) -> None
```

- 覆盖层：无边框、置顶、不抢焦点、鼠标穿透（`endbox.ui.glass.set_click_through`），
  `WA_TranslucentBackground`；视觉为"灵动岛"胶囊（切角、无发光），出现→撑开→收回。
- **内容只有三样真实数据**：账号（头像/昵称/在线）、电池（电量/供电/剩余）、时钟（12/24 小时、秒开关）；
  标题行是 Windows 登录用户名。读不到就显示「未连接」/「—」。**不显示排轴运行状态。**
- 全局热键默认 `Ctrl+Alt+H`（`RegisterHotKey` + nativeEventFilter，失败有日志与界面提示）。
- 控制视图：开关、预览、位置（顶部居中/靠左/靠右）、缩放 0.6–1.4、透明度、显示项、热键录制。
- 关闭时释放窗口与热键；重复开关不泄漏窗口。

## 10. 插件模块契约

**宿主** `endbox/plugins/host.py::PluginHost(ctx, registry)`；**市场** `endbox/plugins/market.py::Marketplace`。

- 插件目录：根 `plugins/`（随程序分发）与数据目录 `plugins/`（自己装，市场安装到 `userdata/plugins/<id>/`）。
- `plugin.json`：`id / name / version / author / description / icon / entry / order / min_app_version / permissions / category / tags / homepage`。
- 生命周期 `on_load/on_enable/on_disable/on_unload`；异常被捕获、写日志、在插件页显示，绝不影响主程序。
- 插件 API：`register_view`、`storage`（私有 JSON）、`event_bus.subscribe`（白名单 `timeline.*`、
  `account.updated`、`ui.theme_changed`）、`account_snapshot()`（需 `account:read`）、
  `timeline_snapshot()`（需 `timeline:read`）。越权抛 `PermissionError` 并记录日志。
- 宿主新增 `remove(plugin_id)`（摘记录 + 取消视图，不删文件）与 `hot_reload()`；市场安装/更新/卸载后调用。

**市场契约（schema v1）**：

```json
{
  "schema": 1,
  "plugins": [{
    "id": "link_notes", "name": "连携备忘录", "version": "0.1.0", "author": "EndBox",
    "description": "记录干员 / 组合的连携触发条件", "category": "timeline",
    "tags": ["排轴", "连携"], "permissions": [],
    "min_app_version": "1.1.0", "size": 3380,
    "download": "packages/link_notes-0.1.0.zip", "sha256": "…", "homepage": ""
  }]
}
```

- 来源：官方源随程序分发（`market/index.json`，相对路径 `packages/*.zip`，离线可装）；第三方源是 http(s) 的
  JSON 索引，由用户添加；多源合并且有 6h 缓存，断网/限流时降级到缓存。
- 安装安全模型：流式下载（超时 + 大小上限）→ sha256 校验 → 安全解压（拒绝 zip slip、盘符、绝对路径、
  符号链接、加密条目、多顶层目录、超量文件/超大解压）→ manifest 校验（id/version/min_app_version 一致）
  → 临时目录 staging → 备份旧目录 → 替换；失败回滚。**只解压落盘，绝不执行市场里的代码。**
- 来源记录写 `userdata/market_state.json`（`market` / `local` / `bundled`）；随程序分发的插件不允许市场覆盖。
- 打包：`python tools/pack_plugin.py --all`（确定性打包，重复打包 sha256 不变）与 `--check` 校验索引一致性。

## 11. 毛玻璃与自动化

- 主窗口毛玻璃是 **Windows 系统实时模糊**（`endbox/ui/glass.py::apply_acrylic`，
  `SetWindowCompositionAttribute` 的 acrylic）：DWM 实时合成并模糊窗口背后的桌面，
  拖动 / 换壁纸自动跟随，不截图、不闪、没有刷新按钮。
- 窗口保留系统窗口样式（`WM_NCCALCSIZE` 里裁掉标题栏与边框），所以四角由 Windows 自己
  画圆角、模糊也一起被裁圆；最大化时关闭模糊、取消圆角铺满。
- 最大化 / 还原用单一进度动画（几何 + 边距 + 圆角一起插值）；最大化时拖顶栏先还原再继续拖。
- 截图 / 自动化测试设 `ENDBOX_NO_GLASS=1`、`ENDBOX_NO_UPDATE_CHECK=1` 得到稳定且不联网的环境；
  `tools/shot.py`、`tools/integration_check.py`、`tests/conftest.py` 已默认设置。
- 数据目录默认是**软件同目录的 `userdata/`**（便携模式），程序目录不可写时退回 `%APPDATA%\EndBox`；
  旧数据首次启动自动迁移；`ENDBOX_DATA_DIR` 可覆盖（沙箱 / 测试用）。

## 12. 版本与更新契约

- 版本唯一源：`endbox/version.py::__version__`（`endbox/__init__.py` 与关于页、侧栏都从这里取）。
  发布时同步改 `version.json`、`CHANGELOG.md`、`tools/version_info.txt`。
- 服务：`endbox.modules.update.UpdateService.check()`（后台线程调用），返回 `CheckResult`：
  `has_update / info(ReleaseInfo) / source / error / cached`。
  检查顺序：GitHub Releases → 仓库 `version.json` → 本地缓存；任何异常都不抛给界面。
- 语义化比较支持 `v` 前缀、缺位补 0、预发布 < 正式；`ignore` 的版本持久化在设置里。
- 设置页「版本与更新」：当前/最新版本、上次检查时间、来源、检查按钮、打开发布页、忽略/取消忽略、更新日志。
- 顶栏 `UpdateHint`：只有「有更新且未忽略」才出现的小黄条，点击跳设置页；启动后延迟几秒静默检查。
- 发布流程见 `docs/update.md`（tag `v*` → 测试 → PyInstaller → Release）。

## 13. 文案 / 确认框 / LaTeX

### 13.1 全部界面文案进一个文件
- 唯一文件：`assets/text/zh-CN.json`（UTF-8，JSON 对象；**同一个 section 里既有嵌套对象也有点号扁平键**，
  修改前先看结构）。代码：`from endbox.core.text import t`，例如 `t("timeline.view.stats.duration")`。
- 只改自己模块 section 的 value；用 edit 局部修改，遇 `FS_STALE_VERSION` 先重读再改；不要整文件覆盖。
- 代码里除 docstring / 注释 / 日志外不得保留中文字面量；`tests/test_foundation.py::test_text_catalog_keys_exist`
  会扫描静态 `t("...")` 的 key 是否存在。
- 改文案时**保持 key 与占位符不变**；运行时改完 JSON：设置 → 数据与登录信息 →「重新加载文案」。

### 13.2 文案风格（必须遵守）
- 说人话、短句；一句话说一件事，不写长从句。
- 不把内部术语端给玩家：用「登录信息」不用「凭据」，用「没启动」不用「未载入/未初始化」，
  用「读到/读不到」不用「解析/载入」，用「按键被系统拦掉」不用「UIPI」。
- 不解释实现、不自我辩解（不写「不编造」「未端到端验证」这类话，改成玩家能懂的「没在真实账号上验过」）。
- 错误 = 发生了什么 + 怎么办，两句以内；提示 = 一句。
- 状态用名词短语；玩家词优先：理智、练度、突破、潜能、连携、终结技。

### 13.3 删除 / 清空 / 重置必须确认
```python
from endbox.ui.kit import confirm
if not confirm(self, title="清空技能轴", message="清空后现在这条序列就没了，确定？", action_text="清空"):
    return
```
`danger=True`（默认）时确认按钮为红色，只用于删除/清空；重置类用 `danger=False`。
默认焦点在「取消」，Esc 取消。所有删除入口都要走它。

### 13.4 LaTeX 公式渲染
```python
from endbox.ui.kit import LaTeXView, render_latex
view.set_latex(r"\frac{172+3}{5} \times 4 = 325", text="(172+3)/5 × 4 = 325")
```
- matplotlib mathtext 渲染（vendored `vendor/`，失败自动退回文本）；**不支持 `\begin{matrix}`**，矩阵写成展开式。

## 14. 验收清单（各模块自查）

- [ ] `python tools/shot.py <你的视图> shots/<name>.png dark` 与 `light` 都能出图，不报错。
- [ ] 切到其他视图再切回来，状态不丢；离开视图后轮询/播放停止（HUD 除外：它只读后台服务）。
- [ ] 服务缺失（`ctx.services.xxx is None`）时视图能打开并显示「没启动」。
- [ ] 网络失败、空数据、权限拒绝都有明确界面反馈；没有伪造数据。
- [ ] 黄色只用于填充；红色只用于删除；没有圆角、投影、发光、渐变。
- [ ] 所有数字真实；空状态是一句完整的话；文案符合 §13.2。
- [ ] 键盘可聚焦、焦点可见；HUD 重复开关不泄漏窗口；排轴运行条与 HUD 互不影响。
- [ ] 没有修改自己范围外的文件；新增依赖 0 个。
