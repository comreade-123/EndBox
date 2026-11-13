# 全局输入服务（`endbox/core/input`）

排轴「按对键才推进」运行态的输入层：Windows 全局低级键盘钩子 + XInput 手柄轮询，
把物理按键统一成**规范 token**（`Q+1` / `HOLD+3` / `PAD_A` …），通过 Qt 信号
回到主线程交给 timeline 等消费方。**不注入、不吞键、不要求游戏窗口在前台。**

| 文件 | 内容 |
| --- | --- |
| `tokens.py` | token 常量表与 `normalize_token`（timeline 依赖，名字不可改） |
| `models.py` | `KeyEvent`、`InputBackendError` |
| `keyboard.py` | `WH_KEYBOARD_LL` 钩子后端、VK→token、`ModifierTracker`、`HoldTracker`（长按） |
| `gamepad.py` | XInput 轮询（Xbox 布局）+ winmm `joyGetPosEx`（DS / 兜底）、按钮位差分 |
| `service.py` | `InputService(QObject)`：信号、生命周期、长按定时器、管理员检测、订阅 |
| `__init__.py` | 对外导出（`InputService` / `KeyEvent` / `normalize_token` / `TOKENS` …） |

## 1. 快速开始

### 1.1 跑起来：3 秒监听（可直接运行）

在仓库根目录保存为 `probe.py`，`python probe.py` 运行，然后按 **Q+1**、**长按 1**、**H**：

```python
# -*- coding: utf-8 -*-
import sys
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication
from endbox.core.input import InputService

app = QApplication(sys.argv)
svc = InputService()                          # 构造不装钩子
svc.token.connect(lambda token: print("TOKEN", token))    # 规范化 token
svc.raw.connect(lambda event: print("RAW  ", event))      # 含松开 / 注入标志
svc.warning.connect(lambda text: print("WARN ", text))    # 非管理员 / 手柄不可用
print("start:", svc.start())                  # 装上全局钩子（幂等）
QTimer.singleShot(3000, app.quit)             # 3 秒后退出
app.exec()
svc.stop()                                    # 卸载钩子、结束钩子线程（幂等）
print("status:", svc.status())
```

### 1.2 接进排轴运行态（timeline 推荐写法）

```python
from endbox.core.input import InputService

self.input = InputService(self)               # 父对象归 Qt 管
self.input.token.connect(self.handle_input_token)   # token: str（主线程）
self.input.raw.connect(self.on_input_event)         # KeyEvent（调试 / 录制）
self.input.warning.connect(lambda text: self.ctx.notify(text, "warn"))
self.input.error.connect(lambda text: self.ctx.notify(text, "warn"))
self.input.set_hold_ms(280)                   # 长按阈值（80–2000ms）
self.input.set_gamepad("off")                 # "off" | "xbox" | "ds"
self.input.start()                            # 进入运行态
...
self.input.stop()                             # 退出运行态（必须调）
```

也可以只订阅 token（首个订阅者会自动 `start()`，最后一个取消后自动 `stop()`）：

```python
from endbox.core.input import InputService

svc = InputService()
sub = svc.subscribe(lambda token: handle(token))   # 自动启动；启动失败抛 InputBackendError
try:
    ...
finally:
    sub.cancel()                                   # 幂等；自动启动的服务随后自动停止
```

`endbox/modules/timeline/input_adapter.py` 的接入点：它先找 `ctx.services.input`，
找不到时 `import endbox.core.input` 并调用 `get_service()`（进程级单例）——两者都提供
`subscribe(callback)`，所以运行时不需要额外装配代码。另外 `endbox.core.input.service`
子模块本身也导出了 `subscribe(callback)`（等价于 `get_service().subscribe(...)`），
用于兼容该适配层的 duck-type 探测，**正常情况下用 `InputService` 或 `get_service()` 即可**。

### 1.3 测试里用 fake backend（绝不安装真实钩子）

```python
from endbox.core.input import InputService, KeyEvent

class FakeKeyboard(KeyboardBackend):
    name = "fake"
    @property
    def available(self): return True
    def start(self, emit): self._emit = emit
    def stop(self): self._emit = None

svc = InputService(keyboard_backend=FakeKeyboard(), admin_check=lambda: True)
svc.start()
svc.keyboard_backend._emit(KeyEvent(token="Q+1", kind="down", vk=0x31))
svc.stop()
```

`tests/test_input.py` 就是这样测的：注入 fake 键盘 / 手柄后端与可推进的假时钟，
不装任何全局钩子、不依赖桌面会话。

## 2. token 表（timeline 按对键只认这些）

键盘（低级钩子，`source="keyboard"`）：

| token | 触发 | 用途（timeline 默认） |
| --- | --- | --- |
| `1` `2` `3` `4` | 数字 1–4 单按（无修饰） | 合法 token；运行态通常不参与步骤匹配（见 §7） |
| `Q+1` … `Q+4` | 按住 Q 再按数字 | 战技（1–4 号位） |
| `E+1` … `E+4` | 按住 E 再按数字 | 连携（1–4 号位） |
| `ALT+1` … `ALT+4` | 按住 Alt 再按数字 | 终结技（1–4 号位） |
| `HOLD+1` … `HOLD+4` | **无修饰**长按数字 ≥ `hold_ms`（默认 280ms） | 终结技的长按路线（与 `ALT+n` 等价） |
| `H` | H 键 | 重击 |
| `F6` | F6 | 提示条变淡 |
| `F7` | F7 | 提示条变实 |
| `F8` | F8 | 退出运行态 |
| `F9` | F9 | 回第一段 |
| `F10` | F10 | 反复开关 |
| `F11` | F11 | 回反复起点 |

手柄（`source="gamepad"`，`set_gamepad` 打开后才轮询）：

| token | Xbox 手柄 | DS 风格（winmm） |
| --- | --- | --- |
| `PAD_A` | A | ×（B2） |
| `PAD_B` | B | ○（B3） |
| `PAD_X` | X | □（B1） |
| `PAD_Y` | Y | △（B4） |
| `PAD_LB` / `PAD_RB` | LB / RB | L1 / R1 |
| `PAD_MOD+PAD_A/B/X/Y` | 按住 LB 再按面键 | 按住 L1 再按面键 |

- `PAD_MOD` 就是左肩键：单独按左肩键发 `PAD_LB`，按住它再按面键发 `PAD_MOD+PAD_A` 这类组合。
- token 信号**只在按下 / 长按**时发一次；松开只进 `raw`（`kind="up"`），不产生动作 token。
- 修饰键本身（Q/E/Alt）按下不发 token（只进 `raw`），避免误判。
- `normalize_token` 也接受旧写法：`q1` / `q+1` / `Q 1` → `Q+1`，`alt3` → `ALT+3`，
  `hold1` → `HOLD+1`，`pad-a` → `PAD_A`，`padmod+a` → `PAD_MOD+PAD_A`；无法识别返回 `""`。
  全部合法 token 见 `TOKENS` / `TOKEN_SET`（共 37 个）。

## 3. API

```python
from endbox.core.input import InputService, KeyEvent, Subscription

class InputService(QObject):
    token   = pyqtSignal(str)      # 规范化 token（按下 / 长按）
    raw     = pyqtSignal(object)   # KeyEvent：down / up / hold 全部事件
    error   = pyqtSignal(str)      # 硬错误（钩子安装失败等，可显示的中文原因）
    warning = pyqtSignal(str)      # 降级提示（非管理员 / 手柄不可用 / 初始化失败）

    enabled: bool                  # 是否已启动
    last_error: str | None
    hold_ms: int                   # 当前长按阈值
    gamepad_mode: str              # off / xbox / ds
    keyboard_backend / gamepad_backend

    def start(self) -> bool: ...           # 幂等；失败返回 False 并发 error
    def stop(self) -> None: ...            # 幂等；PostThreadMessage(WM_QUIT) + Unhook
    def set_hold_ms(self, ms) -> int: ...  # 夹在 80–2000，返回生效值
    def set_gamepad(self, mode) -> str: ...# "off" | "xbox" | "ds"，非法抛 ValueError
    def is_admin(self) -> bool: ...
    def status(self) -> dict: ...          # 只读状态快照（设置页/调试用）
    def subscribe(self, callback) -> Subscription: ...  # callback(token: str)，主线程
```

`KeyEvent`（`frozen dataclass`）：`token: str`、`kind: "down"|"up"|"hold"`、
`modifiers: tuple[str, ...]`、`vk: int`（键盘 VK / 手柄按钮位）、
`timestamp: float`（`time.monotonic()`）、`source: "keyboard"|"gamepad"`、
`injected: bool`（SendInput/keybd_event 合成的事件为 True，真实手动按键为 False）。

`Subscription`：`.active`、`.cancel()`（幂等，可当上下文管理器）。

模块级：`normalize_token(str) -> str`、`is_admin() -> bool`、`get_service() -> InputService`
（进程级单例，懒创建、不自动启动）、`service.subscribe(callback)`（单例订阅的便捷入口、
兼容适配层 duck-type）、`TOKENS` / `TOKEN_SET` / `DIGITS` / `HOLD_TOKENS` 等常量。

## 4. 线程与权限

- **钩子线程**：`SetWindowsHookExW(WH_KEYBOARD_LL)` 在独立线程 `endbox-input-hook`
  里执行 `GetMessageW` 消息循环；回调只做 VK→token 解析与 `KeyEvent` 构造，
  立刻 `CallNextHookEx` 放行——**不阻塞、不碰 Qt 窗口**。事件通过服务内部信号
  （AutoConnection）**排队回主线程**，所以 `token` / `raw` 信号和 `subscribe` 回调
  都在主线程执行，可以直接更新 UI。
- **长按判定**：主线程 QTimer（25ms）驱动纯 Python 的 `HoldTracker`，到点额外发
  `HOLD+n`；钩子线程不参与计时。
- **手柄轮询**：主线程 QTimer（25ms）调 `XInputGetState` / `joyGetPosEx`（都是极轻的
  本地调用）；热插拔会自动重新寻找 0–3 号 XInput 槽位。
- **停止**：`stop()` 先 `PostThreadMessageW(thread_id, WM_QUIT)` 结束消息循环，
  钩子线程在 `finally` 里 `UnhookWindowsHookEx` 并退出，主线程 `join(2s)`；
  重复 `start()/stop()` 不会重复装钩子或泄漏线程。
- **权限**：低级键盘钩子**不需要管理员**，普通用户即可。但 Windows **UIPI** 会挡：
  如果游戏/目标窗口以管理员权限运行，而非管理员进程装的钩子收不到那些按键。
  `InputService.start()` 检测到非管理员时会发一条 `warning`（不阻塞、不失败），
  设置页建议提示用户「以管理员身份运行 EndBox」。
- 钩子只**读**按键，不吞键：游戏照常收到所有输入；不注入、不修改游戏内存。

## 5. 真实钩子自检（`_spike/`）

临时脚本 `_spike/input_hook_probe.py`（保留，可随时删）做了两件事，3 秒内跑完：

1. 装上真实钩子后，用 `keybd_event`/`SendInput` 合成按键（`LLKHF_INJECTED`），
   覆盖 `1`、`Q+1`、`H`、`F8` 和「按住 1 超过 280ms」；
2. 打印捕获到的 token / KeyEvent / 是否 `injected`，以及 `stop()` 后钩子线程是否结束。

结果见交付说明：真实钩子能收到**手动/合成按键**，`Q+1` 会正确归一化，
长按 1 会先发 `1` 再发 `HOLD+1`，`stop()` 后线程干净退出。

## 6. 限制（务必在设置页/运行态提示里写清楚）

| 场景 | 行为 / 限制 |
| --- | --- |
| 独占全屏游戏 | 低级键盘钩子位于输入路由之前，**通常仍能收到按键**；但无边框覆盖层（提示条/HUD）可能画不到游戏上面，这是绘制层限制，不是输入层。个别游戏用 Raw Input 独占并主动过滤合成输入，可能收不到（此时用 `raw` 调试确认）。 |
| 反作弊 / 内核级反作弊 | 本服务不注入、不改内存、不发送按键，只是在系统层读键盘事件；但全局钩子本身可能被部分竞技游戏的反作弊标记为可疑行为，**风险自负**。不要在排位/竞技场景依赖它。 |
| 管理员游戏 + 非管理员 EndBox | UIPI 会挡掉按键（收不到，不报错）；`warning` 已提示，以管理员身份运行 EndBox 可解决。 |
| 远程桌面 / 锁屏 / 切换用户 | 钩子只覆盖当前交互桌面；RDP/锁屏期间收不到按键，属于正常现象。 |
| 输入法 / AltGr | 钩子按虚拟键码上报，不关心输入法组合；AltGr 在 Windows 上表现为 `Ctrl+Alt`，仍按 `ALT` 处理（`Ctrl` 未映射，不会出现在 token 里）。 |
| 左右 Alt | `VK_MENU` / `VK_LMENU` / `VK_RMENU` 都归一化为 `ALT`。 |
| 手柄支持范围 | XInput：Xbox 360 / One / Series 及被 Steam 等映射成 XInput 的手柄（0–3 号槽，自动热插拔）；DS 风格走 winmm `joyGetPosEx`（DirectInput 枚举到才可用），只映射 6 个按钮，**不支持扳机/摇杆/震动**；DS5 的触控板、自适应扳机等不在此服务范围内。 |
| 手柄与键盘语义 | 手柄 token 是 `PAD_*`，timeline 的步骤 token 是 `Q+n/E+n/ALT+n/H`；消费方需要自己做映射（当前 timeline 运行态默认只吃键盘 token）。 |
| 无新依赖 | 全部用标准库 `ctypes` + PyQt6；非 Windows 平台 `start()` 返回 False 并发 `error`（`NullKeyboardBackend` 降级），不会崩。 |

## 7. 与 timeline 运行态的注意事项

- **无修饰数字**：按下就发 `1`，长按 280ms 再发 `HOLD+1`（符合"额外发"的约定）。
  timeline 的 `RunEngine` 只把当前步骤的 token 当作"按对"，所以**如果运行态不想让
  无修饰数字触发"按错了"提示，消费端应忽略 `DIGITS` 这类 token**（只处理 `Q+/E+/ALT+/HOLD+/H/F*`），
  或者只订阅 `raw` 自行过滤。这是消费方的匹配策略，输入服务只负责如实上报。
- **修饰键组合不会触发长按**：按住 Q/E/Alt 时按数字是组合键，即使长按也不会发
  `HOLD+n`，避免一次按键同时命中战技/连携/终结技两个语义。
- **`F6`–`F11` 是运行控制键**：`token` 信号会照常发出，由 timeline 的
  `handle_input_token` 决定语义（退出/回第一段/变淡/变实/反复开关/回反复起点）。
- **退出运行态必须 `stop()`**：否则钩子会一直留在系统里；本服务不自动跟随视图切换。
- 日志：`logs/endbox.log` 里 `core.input.*` 记录启动/停止/降级原因，便于排查权限与
  手柄问题。
