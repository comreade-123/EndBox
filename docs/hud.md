# HUD 覆盖层（模块三）

EndBox 的「灵动岛」HUD：一个无边框、置顶、**鼠标穿透**的独立覆盖层窗口。
它只负责 **账号 / 电池 / 时钟**，标题行显示 **当前 Windows 登录用户名**；
排轴运行态由排轴模块自己的顶部提示条负责，两者互相独立。
动画骨骼移植自 `zmd-charge-main`（EndfieldCharge 的 Avalonia 实现），视觉按终末地规则改写。

- 两种布局：**紧凑**（面板 560×64 单行）/ **标准**（面板 720×88 两行：账号+时钟 / 电池详情）。
- 三项独立开关、锚点 + 像素偏移、缩放 0.6–1.6、透明度 30%–100%、多显示器选择。
- 全局热键可在控制页**录制**新组合，注册失败自动回退旧组合并提示原因。
- 防闪烁：内容指纹去重 + 固定尺寸 + 只改必要 Win32 属性 + 动画限帧。

## 1. 怎么用

| 操作 | 说明 |
| --- | --- |
| `Ctrl+Alt+H` | 全局热键：任意界面开关覆盖层（可在控制页录制新组合） |
| 侧边栏「HUD」 | 控制页：预览、显示项、模式与位置、热键、状态 |
| 设置 → HUD | 同一个服务的另一个入口（开关 / 热键提示） |

- 覆盖层**不接收鼠标**：点击会穿透到下面的游戏（`WindowTransparentForInput` +
  Win32 `WS_EX_TRANSPARENT` 双保险）；所有调整都在控制页完成。
- 不进任务栏（`Tool` 窗口）、不抢焦点（`WA_ShowWithoutActivating` +
  `WindowDoesNotAcceptFocus`）、不参与 Alt+Tab。
- 关闭覆盖层或退出程序时，窗口隐藏 / 释放、全局热键注销；反复开关复用同一个窗口实例。

## 2. 显示内容（三项独立开关 + 标题用户名）

| 显示项 | 内容（全部真实数据） | 数据来源 | 缺失时 |
| --- | --- | --- | --- |
| 账号头像与昵称 | 头像 + 昵称 + 在线点（黄点=在线，空心=离线） | `account.state` / `account.updated` | 「未连接」+ 黄旗 |
| 电池详情 | 电量百分比 + 10 格刻度 + `外接电源 / 电池供电 / 充电中` + `剩余 2h05m` | ctypes `GetSystemPowerStatus`（每 5s） | `—`，刻度空心 |
| 时钟 | 24/12 小时可切换、秒开关；`QTimer` 每秒刷新 | 系统本地时间 | 不会缺失 |
| 标题行（非开关项） | `/// ENDBOX · HUD` + **当前 Windows 登录用户名**（`getpass.getuser()`，失败回退 `GetUserNameW`，都取不到显示 `—`；过长自动 elide） | 系统 | `—` |

三项全关时显示空状态：「没有可显示的项 · 到控制页打开数据源」。

**与排轴模块的分工**：HUD 不读取、不订阅任何排轴事件，也不显示排轴时间 / 当前技能 /
下一个技能 / 队伍 / Buff / 进度条 —— 这些全部属于排轴模块自己的运行态提示条。
`HudService` 只订阅 `account.state / account.updated`，`snapshot()` 里也没有 `timeline` 键；
测试里有源码级回归（HUD 包与控制页不得 import 排轴模块或订阅 `Topics.TIMELINE*`）。

`compact` 模式一行放 账号 / 电池 / 时钟（电池只显示百分比 + 刻度 + AC/CHG 标记）；
`standard` 模式两行：上排 账号 / 时钟，下排 电池详情（含供电方式与剩余时间）。
两行中某一行没有可显示项时自动折叠为单行，不浪费高度。

## 3. 布局与自定义

| 项 | 范围 / 取值 | 设置键 |
| --- | --- | --- |
| 布局模式 | `compact`（画布 560×76，面板 64）/ `standard`（画布 720×96，面板 88） | `hud.mode` |
| 锚点 | 顶部居中 / 靠左 / 靠右（距屏幕可用区顶部 18px） | `hud.anchor` |
| 偏移 | X: −800…800 px，Y: −400…800 px | `hud.offset_x` / `hud.offset_y` |
| 缩放 | 0.60–1.60（±0.05） | `hud.scale` |
| 透明度 | 30%–100%（±4%，窗口级 `setWindowOpacity`） | `hud.opacity` |
| 显示器 | 下拉选择当前系统里的任一显示器（按 `QScreen.name()` 记住，找不到回主屏） | `hud.screen` |
| 显示项 | 账号 / 电池 / 时钟 三项独立开关 | `hud.show_account` / `hud.show_battery` / `hud.show_clock` |
| 时钟格式 | 24/12 小时 + 秒开关 | `hud.clock_12h` / `hud.clock_seconds` |
| 热键 | 控制页录制新组合；注册失败回退旧组合 | `hud.hotkey` |

「恢复默认外观」会一次重置模式 / 锚点 / 偏移 / 缩放 / 透明度 / 显示器 / 全部显示项。
预览区与桌面覆盖层是**同一份** `HudCanvas` 绘制代码，切显示项/模式会立刻反映到预览。

## 4. 视觉与动画

参考实现是 560×60 的全圆胶囊 + 光晕波纹；这里按终末地规则改写：

| 参考实现 | EndBox HUD |
| --- | --- |
| 全圆胶囊 `CornerRadius=30` | **切角方框**（左上、右下各切 9–10px），工业感 |
| 发光圆环波纹 | 3 圈 1px 同心切角方框，从左侧方块扩散并淡出；无发光/渐变 |
| 电量圆环（圆弧进度） | 10 格电量刻度条，已充格墨色实心，低电变黄 + 黄旗 |
| 电标圆形→方形形变 | 左侧黄色切角方块 + `hud` 线图标（`paint_icon`） |
| 标题态 / 数字态 | 标题态显示用户名 → 数据态显示 账号/电池/时钟 |
| 胶囊高度 60→90→60 | 面板高度 紧凑 56→76→64 / 标准 80→96→88 |

动画时间线（`QVariantAnimation` 驱动线性进度，`curve_at` 分通道求值，总长约 720ms；
入场按 24 帧量化 ≈30fps，退出 8 帧 ≈33fps）：

```
0.00–0.28  整体弹出（scale 0.74→1，snap）
0.18–0.52  面板撑高（mech），方块从正中滑到左侧（ease）
0.20–0.70  波纹扩散（同一条 slide 曲线跟随方块，淡入 0.20–0.28、淡出 0.62–0.80）
0.24–0.44  标题淡入（/// ENDBOX · HUD + Windows 用户名）；0.56–0.74 标题淡出
0.62–0.94  面板收窄 + 数据行淡入
退出：     数据先退场，面板收窄 + 整体缩小 + 淡出（mech，240ms）
```

- 设置 `app.animations=false` 时所有阶段直接跳到稳定态（不闪、不动）。
- 描边一律 1px（`paint.hairline` / `QPen(..., 1)`），没有圆角、投影、渐变。
- 黄色（`pal.signal`）**只做填充**：左侧方块、在线标记、低电小旗。

## 5. 闪烁排查（结论）

**症状**：覆盖层在桌面/游戏上运行时能看到周期性轻微闪一下（尤其静止、只刷新时钟时）。

**根因**（旧实现）：

1. 服务每 60ms 就把快照推给窗口，`HudWindow.apply_snapshot()` **无条件**调用
   `canvas.update()` → 整窗（透明分层窗口）全量重绘，即使内容一个字都没变；
2. 同一函数**每次**都调用 `setWindowOpacity()`。在 Windows 上这会让 DWM 重新设置
   分层窗口的 alpha/合成属性，是最容易看到"闪一下"的一步；
3. 入场动画用 `QVariantAnimation` 默认 60fps，驱动一个 `WA_TranslucentBackground`
   窗口每秒 60 次整窗重绘；
4. 窗口尺寸用 `resize()` 且每次快照都对比，缩放/模式切换时会有额外重排。

**修复**：

| 修复 | 实现 |
| --- | --- |
| 内容指纹去重 | `state.render_key()`（显示内容）＋`state.snapshot_key()`（完整快照）；没变就不重画/不广播 |
| 窗口属性只在真变化时改 | `apply_snapshot` 里记 `_opacity_applied` / `_geometry_key`，透明度与尺寸不变就不调 Win32 |
| 固定尺寸 | 窗口用 `setFixedSize()`，只有模式/缩放变化才重设 |
| 动画限帧 | 入场 24 帧（约 30fps）、退出 8 帧，不再 60fps 刷透明窗口 |
| 不重复 show/hide | 已显示时 `show_overlay` 只重播动画；隐藏时不再触发重绘 |
| 隐藏时不白跑 | 窗口隐藏时内容更新只改内存状态，画布不重画 |

**量化验证**（`python _spike/hud_flicker_probe.py`，实测真实 Win32 窗口）：

| 场景 | 画布 paint 次数 | `setWindowOpacity` 次数 |
| --- | --- | --- |
| 入场动画 720ms | 26 次 / 1.0s（≈24 帧） | 0 |
| 显示但静止（只有 1s 时钟） | **3 次 / 3.0s（1/s）** | 0 |
| 窗口隐藏 | **0 次 / 2.0s** | 0 |
| 账号状态每秒变化（内容真的变了） | 5 次 / 3.0s | 0 |
| 重复 show/hide ×5 | 窗口实例复用、顶层窗口始终 1 个、stop 后热键已注销 | — |

旧实现同样的静止场景 ≈ 每秒 16 次整窗重绘 + 16 次 `setWindowOpacity`；
现在只剩时钟真的走秒时的 1 次画布更新，闪烁随之消失。

## 6. 全局热键实现

`endbox/modules/hud/hotkey.py`：

- `RegisterHotKey(hwnd, id, MOD_CONTROL|MOD_ALT|MOD_NOREPEAT, 'H')`，hwnd 来自一个隐藏的
  `QWidget(Tool)`，绝不显示；
- `QAbstractNativeEventFilter` 接收 `WM_HOTKEY`（`0x0312`）并核对热键 id；
  **PyQt6 6.5+ 的虚函数必须返回 `(bool, sip.voidptr)`**，只返回 bool 会打断事件循环；
- 控制页「录制热键」：点击后**先注销旧热键**（否则系统会吞掉按键、Qt 收不到），
  然后从 `QKeyEvent` 生成组合（`spec_from_qt`：字母/数字/F1–F24/Space/Tab/Enter/Esc/方向键…）；
  失败原因（如占用 1409）显示在控制页，并**自动回退旧组合**；Esc 或点别处 = 取消并恢复旧组合；
- 失败分支：非 Windows、没有 QApplication、组合非法、被别的程序占用（1409）、
  user32 调用失败 —— 都只记录日志 + `failed` 信号 + 控制页黄色提示，不抛异常、不崩；
- `HudService.stop()` / 退出应用时 `removeNativeEventFilter` + `UnregisterHotKey`。

## 7. 数据来源

| 字段 | 来源 | 缺失时 |
| --- | --- | --- |
| 账号 `nickname / online / avatar_path / provider` | `account.state` / `account.updated` | 「未连接」 |
| 电池 `percent / ac / charging / remaining_s` | ctypes `GetSystemPowerStatus`，每 5 秒 | `—` |
| 时钟 | `QTimer` 每秒 + 本地时间（12/24h、秒开关） | 不会缺失 |
| 标题用户名 | `state.current_username()`：`getpass.getuser()` → `GetUserNameW` | `—` |

`HudService.snapshot()` 的公开键（供 home / settings / 插件兼容使用）：
`enabled / window_created / window_visible / hotkey / account / battery / clock / username /
anchor / offset_x / offset_y / screen / mode / scale / opacity / clock_12h / clock_seconds /
show / animations`。**不再包含 `timeline`**；home 只用 `hud.enabled`，settings 用
`hotkey` 与 `enabled`，均不受影响。

## 8. 已知限制

- 只在 Windows 上有全局热键与电池读数；其它平台 HUD 仍可显示，但热键与电池字段降级。
- 独占全屏（exclusive fullscreen）游戏可能盖住任何置顶窗口；请用无边框 / 窗口化全屏。
  这是 Windows 的窗口层级限制，与本模块无关。
- HUD 鼠标穿透，窗口本身不能被点击；位置/尺寸只能从控制页调整。
- 台式机没有电池时 `GetSystemPowerStatus` 返回"无电池"，HUD 显示 `—`（不编造 100%）；
  `BatteryLifeTime` 未知或交流供电时剩余时间显示 `—`。
- 热键被占用（错误 1409）时需要换组合或关掉占用它的程序；EndBox 不会强抢，会回退旧组合。
- 账号头像来自账号模块给的 `avatar_path`；没有头像时显示昵称首字占位框，不画假头像。
- 覆盖层不抓屏、不注入游戏进程；只显示 EndBox 自己的账号 / 系统数据。

## 9. 文案（`assets/text/zh-CN.json` 的 `hud` section）

HUD 用户可见文字**全部**集中在 `assets/text/zh-CN.json` 的 `hud` 分区，代码统一用
`from endbox.core.text import t` 取值；代码里不再有中文界面字面量
（`tests/test_hud.py::test_hud_code_has_no_chinese_ui_literals` 会用 AST 扫描 HUD 源码做回归，
docstring / 注释除外）。占位符用 `{name}` 形式，例如：

```python
t("hud.title")                                      # "/// ENDBOX · HUD"
t("hud.overlay.empty")                              # 空状态
t("hud.status.battery", detail="76% · 外接电源")     # 电池行
t("hud.errors.hotkey_fallback_ok", error=..., spec=...)
```

用到的 key 家族：`hud.title / eyebrow / view_title / sidebar_label / description / strip`、
`hud.modes.*`、`hud.anchors.*`、`hud.overlay.*`、`hud.account.*`、`hud.battery.*`、
`hud.preview.*`、`hud.display.*`、`hud.layout.*`、`hud.hotkey_panel.*`、`hud.status.*`、
`hud.buttons.*`、`hud.topbar.*`、`hud.fields.*`、`hud.empty.*`、`hud.about.*`、
`hud.notify.*`、`hud.errors.*`。
`hud.title` 就是覆盖层标题行的前缀（`/// ENDBOX · HUD`，后面跟 Windows 用户名）。
JSON 只局部 `edit`，改完重启或在设置页「重新加载文案」；其它模块的 section 不动。

## 10. 自检

```bash
python -m compileall -q endbox
python tools/shot.py hud shots/hud_view_dark.png dark
python tools/shot.py hud shots/hud_view_light.png light
python run_tests.py tests/test_hud.py -q
```

`tests/test_hud.py`（62 个用例）覆盖：账号降级 / 电池详情与剩余 / 12–24h 时钟 / 用户名来源、
"排轴内容不在 HUD"（state 无排轴函数、快照无 timeline 键、源码不 import 排轴模块、不订阅排轴主题）、
文案集中化（必需 key 存在、占位符可用、源码无中文 UI 字面量）、
重绘与广播去重键、热键解析与 Qt 按键录制、原生事件过滤器返回值契约、注册失败回退旧组合、
电池读取容错、服务开关幂等与账号事件聚合、设置项落盘与夹紧、stop 注销热键/解除订阅、
两种模式窗口几何与复用不泄漏、**相同快照不重复 `setWindowOpacity` / 不重复重画（闪烁回归测试）**、
画布两种模式布局不越界 / 用户名标题 / 各状态绘制、控制视图有 / 无服务的构建与录制流程。

临时脚本（开发期自查，可删）：

```bash
python _spike/render_hud_frames.py    # 两种模式 × 多状态 × 动画帧出图 shots/hud_overlay_{dark,light}.png
python _spike/render_view_states.py   # 控制页整页 / 服务缺失 / 热键失败 出图
python _spike/hud_flicker_probe.py    # 重绘次数探针（闪烁排查的量化证据）
python _spike/check_hud_text_literals.py  # 扫描 HUD 源码里残留的中文 UI 字面量
```

## 11. 文件

```
endbox/modules/hud/__init__.py    create_service(ctx)
endbox/modules/hud/service.py     HudService：生命周期 / 热键录制与回退 / 账号聚合 / 设置
endbox/modules/hud/overlay.py     HudWindow + HudCanvas + 两种布局 + 三段式动画编排（标题=用户名）
endbox/modules/hud/hotkey.py      RegisterHotKey + QAbstractNativeEventFilter + Qt 按键录制
endbox/modules/hud/battery.py     GetSystemPowerStatus（ctypes，含剩余时间）
endbox/modules/hud/state.py       纯函数：账号/电池/时钟文案、用户名、重绘去重键
endbox/ui/views/hud.py            HudView 控制页（预览 / 显示项 / 模式位置 / 热键录制 / 状态）
tests/test_hud.py                 HUD 模块测试
```

排轴运行态提示条属于排轴模块（`endbox/modules/timeline/**` 与它自己的 overlay / 视图），
不在本文档与 HUD 模块范围内。
