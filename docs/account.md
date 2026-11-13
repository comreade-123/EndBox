# 账号中心（模块一）

> 交付范围：`endbox/modules/account/**`、`endbox/ui/views/account.py`、`tests/test_account.py`、本文档。
> 契约见 `docs/CONTRACT.md` §7。本版把官服「Token 输入」改成了**森空岛 App 扫码登录**（B 服扫码保留）。

## 1. 模块结构

| 文件 | 职责 |
| --- | --- |
| `endbox/modules/account/__init__.py` | 包入口，导出 `AccountService` 与 `create_service(ctx)` |
| `endbox/modules/account/models.py` | 契约状态结构、`Role` / `PlayerProfile` 数据类、UID 脱敏、演示数据 |
| `endbox/modules/account/skland.py` | 森空岛扫码：创建二维码 / 轮询 / 换 token / grant / cred / 签名请求 / 绑定解析 / 凭据存储 |
| `endbox/modules/account/bilibili.py` | B 服扫码：generate / poll、Cookie 解析与 DPAPI 存储、B 站昵称头像 |
| `endbox/modules/account/qr.py` | 用 vendor 的 `QRCode` 生成 `list[list[bool]]` 矩阵给 `QRCanvas` |
| `endbox/modules/account/service.py` | `AccountService(QObject)`：异步刷新、扫码状态机回调、事件广播、演示模式 |
| `endbox/ui/views/account.py` | `AccountView(BaseView)`，`key="account"` / `order=10` |

服务由 `endbox/app.py` 通过 `endbox.modules.account.create_service(ctx)` 装配；
视图通过 `self.ctx.services.account` 访问，**服务为 None 时页面仍可打开**并显示「账号模块未载入」。

## 2. 服务 API（契约 §7 + 扫码扩展）

```python
class AccountService(QObject):
    state: dict[str, Any]          # 只读契约状态，字段见下
    def refresh(self, *, on_done=None, on_error=None) -> None
    def snapshot(self) -> dict     # 深拷贝，供插件白名单 / HUD 只读使用
    def logout(self) -> None
    def stop(self) -> None
    # 森空岛扫码（视图驱动状态机，服务只负责网络与落盘）：
    def skland_scan_start(self, *, on_done=None, on_error=None) -> None      # -> ScanQr
    def skland_scan_poll(self, scan_id, *, on_done=None, on_error=None) -> None  # -> ScanStatus
    def skland_scan_confirm(self, scan_code, *, on_done=None, on_error=None) -> None  # 换 cred + 拉绑定
    # B 服扫码：
    def bili_generate(self, *, on_done=None, on_error=None) -> None
    def bili_poll(self, qrcode_key, *, on_done=None, on_error=None) -> None
    def commit_bili_login(self, cookies, *, on_done=None, on_error=None) -> None
    # 其他：
    def set_demo_mode(self, enabled: bool) -> None
    def has_credentials(self) -> bool
    def demo_enabled(self) -> bool
```

`state` / `snapshot()` 字段（必选齐全，缺失用 None / 0 / []）：

```python
{
  "online": bool, "provider": "skland" | "bilibili" | "",
  "nickname": str | None, "uid_masked": str | None, "avatar_path": str | None,
  "roles": [{"name": str, "level": int | None, "server": str | None,
             "class": str | None, "element": str | None}],
  "last_sync": int, "demo": bool, "error": str | None,
}
```

事件：每次状态变化都广播
`account.state`（`nickname / online / avatar_path / provider / roles`）与
`account.updated`（`snapshot=state` 深拷贝）。

启动/刷新时：有 `skland.credential` 就先读绑定列表（cred 过期时静默刷新一次 token 再重试）；
否则有 B 站 Cookie 就拉 B 站昵称/头像；都没有就显示未连接。失败保留凭据，`state["error"]`
写人话，界面显示黄色提示条 + 重试入口。

## 3. 官服：森空岛端到端扫码（社区接口，非官方 API）

### 3.1 实际可用的端点（2026-11-13 实测）

| 步骤 | 请求 | 结果 |
| --- | --- | --- |
| 1. 生成扫码 | `POST https://as.hypergryph.com/general/v1/gen_scan/login`，body `{}`，头 `dId` / `x-requested-with: com.hypergryph.skland` | `data.scanId` + `data.scanUrl`（`hypergryph://scan_login?scanId=...`） |
| 2. 轮询 | `GET https://as.hypergryph.com/general/v1/scan_status?scanId=...` | `status == 0` = 已确认 + `data.scanCode`；否则 `msg` 文案区分未扫码 / 已扫码待确认 / 已过期 / 已取消 |
| 3. 换 Token | `POST https://as.hypergryph.com/user/auth/v1/token_by_scan_code`，body `{"scanCode": ...}` | `data.token`（鹰角 OAuth Token） |
| 4. 授权码 | `POST https://as.hypergryph.com/user/oauth2/v2/grant`，body `{"appCode": "4ca99fa6b56cc2ba", "token": ..., "type": 0}` | `data.code` |
| 5. 换 cred | `POST https://zonai.skland.com/web/v1/user/auth/generate_cred_by_code`，body `{"code": ..., "kind": 1}`，头 `dId/platform/timestamp/vName` | `data.cred`、`data.token`（签名 Token）、`data.userId` |
| 6. 读绑定 | `GET https://zonai.skland.com/api/v1/game/player/binding`，签名头 | `data.list[].appCode / bindingList[]`，过滤 `endfield` 得角色 |

> 任务描述里写的 `gen_scan_login_qrcode` / `scan_login_status` / `scan_login` 是**猜想名**；
> 实际接口是上表的 `gen_scan/login`、`scan_status`、`token_by_scan_code`。已用真实网络实测第 1、2 步：
> 生成 `scanId=3c64d7b5…`、`scanUrl=hypergryph://scan_login?scanId=...`，轮询返回
> `{"status":100,"msg":"未扫码"}`；第 3–6 步因无真实账号，只能用单测覆盖解析与错误分支。

### 3.2 签名算法

社区实现（见 §9 参考）：

```text
timestamp = 服务器时间 - 2 秒
sig_headers = {"platform": "1", "timestamp": timestamp, "dId": device_id, "vName": "1.21.0"}
sign = md5(hmac_sha256(sign_token, path + query + timestamp + json(sig_headers)).hexdigest()).hexdigest()
请求头 = {cred, platform, timestamp, dId, vName, sign}   # sign_token 只参与计算，不发送
```

- 时间优先用响应 `Date` 头纠正本机偏差（±5 分钟内有效），签名前统一取服务器时间。
- `dId` 为本地生成的稳定匿名设备 ID（`secure_store["skland.device_id"]`）。
  本工具**不做官方设备验证**（AUTO-MAS 的 DES/RSA 设备注册流程未引入），
  实测生成二维码 / 轮询不需要设备验证；grant/cred/绑定可能需要，属于未验证风险。

### 3.3 Token / cred 存储

`save_credential()` 只把 `cred / token(sign) / userId / oauthToken` 四项写入
`secure_store["skland.credential"]`（JSON 后再经 DPAPI 加密）。
`logout()` 删除该键与 B 站 Cookie；`last_sync / provider` 仍放普通设置（不含凭据）。

### 3.4 失败分支（均为界面可读中文）

- 生成二维码失败 / HTTP 4xx：`森空岛生成二维码失败（status …）：…`
- 网络失败：`连接森空岛失败（生成二维码/查询扫码状态/…）：…` + 重试建议
- 状态异常文案：`森空岛扫码状态异常：…`（提示刷新二维码）
- 确认后无 `scanCode`：`森空岛扫码已确认，但没有返回扫码凭证。`
- 换 Token 过期：`扫码凭证已失效，请重新扫码。`
- 绑定列表没有终末地角色：`森空岛账号已连接，但没有找到《终末地》角色绑定。`

## 4. B 服（哔哩哔哩扫码，保留）

1. `GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate` 拿 `data.url` + `data.qrcode_key`。
2. `qr.make_matrix(url)` → `QRCanvas.set_matrix()`。
3. 每 2s `GET .../web/qrcode/poll?qrcode_key=...`：`0` 成功 / `86101` 未扫码 / `86090` 已扫码待确认 / `86038` 过期。
4. 成功后从 `data.url` query 取 `SESSDATA`、`bili_jct`、`DedeUserID`、`DedeUserID__ckMd5`，
   分别以 `bili.*` 键经 DPAPI 存入 `secure_store`；再用 `nav` 接口取 `uname / mid / face`。
5. B 站身份不等于终末地角色数据：界面明确提示「角色数据仍需森空岛扫码」，不伪造角色。

轮询在离开视图 / 成功 / 过期 / 取消时停止；连续 5 次网络错误自动停止并提示刷新。

## 5. 界面

两栏阅读式布局：

- **连接状态带**：`ON / OFF / DEMO` 编号块、来源、同步时间、角色数量、演示开关、刷新、
  唯一红色「退出登录」。
- **官服 · 森空岛扫码卡**：160px 二维码（白底黑模块，深色主题也可扫）、扫码指引、
  实时状态（等待扫码 / 已扫码待确认 / 已过期 / 换取凭据中）、剩余时间（`2:58` 真实倒计时）、
  刷新 / 取消按钮；**过期自动换码**（最多连续 3 次，之后给手动刷新）。
- **B 服 · 扫码登录卡**：同样的扫码组件与状态机，保留 B 站身份流程。
- **玩家信息卡**：头像（无图用昵称首字）、昵称、脱敏 UID、来源、上次同步时间、
  编号角色列表（`编号 / 干员 / 服务器 / 职业 / 属性 / 等级`，缺值 `—`）；未连接时斜线纹空状态。
- **演示模式**：状态块变黄色 `DEMO`，下方黄色 InfoFlag「演示数据，非真实账号」；
  不触碰真实凭据，关闭后立即回到真实状态。
- **数据来源与安全**：说明第三方接口、DPAPI、轮询/过期策略。

视觉约束：容器直角 + 细线；只有按钮右下切角；黄色只做填充；红色只在「退出登录」；
没有圆角、投影、发光、渐变。二维码为保证可扫性固定黑白（其余部件走主题色）。

## 6. 安全

- 凭据键：`skland.credential`、`skland.device_id`、`bili.sessdata`、`bili.bili_jct`、
  `bili.dedeuserid`、`bili.dedeuserid__ckmd5`；全部只经
  `endbox.core.secure_store`（Windows DPAPI，绑定当前用户）落盘。
- 凭据不出现在日志、错误文案与 `account.updated` 事件里；UID 在状态与界面都脱敏。
- 二维码内容（`scanUrl`）不含凭据；头像缓存只保存公开头像图片。

## 7. 测试与自检

```bash
python -m compileall -q endbox
python run_tests.py tests/test_account.py -q     # 等价于 PYTHONPATH=vendor python -m pytest
python tools/shot.py account shots/account_dark.png dark
python tools/shot.py account shots/account_light.png light
```

测试不联网（42 个），覆盖：DPAPI 存储往返且明文不落盘、契约 state 字段、UID 脱敏、
设备 ID 稳定持久、扫码创建/状态/Token/授权码/cred 解析与错误文案、扫码状态四态与未知状态、
签名头算法（独立实现逐字节比对）、cred 过期刷新重试、绑定列表过滤终末地、B 站扫码状态与 Cookie、
二维码矩阵、服务演示模式 / 登出 / 无凭据刷新 / stop 后忽略迟到回调，
以及视图层「退出登录必须先确认、取消时凭据与状态都不变、确认后才清除凭据」。

额外手工验证（真实网络 + 真窗口）：
- 森空岛生成二维码 + 轮询「未扫码」：`shots/account_skland_live.png`
- 隐藏视图后轮询停止、切回恢复；过期后自动刷新出新二维码（状态机断言）
- B 服二维码生成 + 轮询：`shots/account_bili_live.png`

## 8. 界面截图

- `shots/account_dark.png`、`shots/account_light.png`：未连接（默认）状态
- `shots/account_demo_dark.png`、`shots/account_demo_light.png`：演示数据 + 角色列表
- `shots/account_skland_live.png`：真实森空岛扫码（二维码已生成、轮询中、剩余 2:58）
- `shots/account_bili_live.png`：B 服扫码（二维码已生成、轮询中）
- `shots/account_missing.png`：`ctx.services.account is None` 时的「模块未载入」
- `shots/account_confirm_dark.png`、`shots/account_confirm_light.png`：退出登录确认框（danger 红色只给这个动作）
- `shots/account_qr_check.png`、`shots/account_qr_live.png`：二维码矩阵 / 旧版 B 服扫码渲染检查
- `shots/account_1040.png`：最小窗口布局

## 9. 参考（第三方资料，仅作流程参考，未照抄代码）

- 森空岛 API 文档（Codeberg）：<https://codeberg.org/Yalishidoorde/Skland_API>
- AUTO-MAS 森空岛扫码实现：
  <https://github.com/AUTO-MAS-Project/AUTO-MAS/blob/main/app/tools/skland.py>、
  <https://github.com/AUTO-MAS-Project/AUTO-MAS/blob/main/app/api/skland_qr.py>
- 鹰角通行证：`https://as.hypergryph.com/`
- 森空岛业务接口：`https://zonai.skland.com/`
- B 站 passport：<https://passport.bilibili.com/>
- 二维码库：`endbox/thirdparty/qrcode`（vendor，见其 `LICENSE`）

## 10. 已知限制

1. **无真实账号**：扫码第 3–6 步（token_by_scan_code → grant → cred → binding）未端到端验证；
   解析、错误分支、签名算法由单测覆盖。接口随时可能变更。
2. **设备验证**：未实现官方 `dId` 设备注册流程；若上游对 grant/cred 强制设备校验，
   会表现为「获取 cred 失败」，需要后续补充设备验证（目前失败提示明确、可重试）。
3. **角色字段**：绑定列表只给 `uid/nickName/channelName`，等级/职业/属性通常没有，
   界面显示 `—`，不编造。官服无可靠头像接口，回退昵称首字。
4. **B 服**：只完成 B 站身份；终末地角色仍需森空岛扫码，界面与 `state["error"]` 均明确提示。
5. 部件库 `QRCanvas` 深色主题会反色，本视图用固定白底黑模块的子类规避；
   其他模块若直接用 `QRCanvas` 仍需注意。

## 11. 收尾（2026-11-14）

- **文案外部化**：账号视图与账号模块里所有用户可见中文（标题、标签、按钮、状态、空状态、
  错误与提示、tooltip、确认框文案）都集中在 `assets/text/zh-CN.json` 的 `account` 节（247 条），
  代码统一 `from endbox.core.text import t` 取值；占位符用 `{name}`（如 `account.status.field_sync`
  的 `{time}`、`account.scan.retrying` 的 `{count}/{max}`）。只改了 `account` 节，未动其他模块的 key。
- **删除/退出确认**：点「退出登录」先弹 `endbox.ui.kit.confirm(..., danger=True)`；取消则什么都不做，
  确认后才 `service.logout()` 清除森空岛凭据与 B 站 Cookie。账号页没有其他删除动作。
- **无旧 Token 输入 UI**：视图里没有 `QLineEdit` / token 输入卡，官服登录是森空岛 App 扫码。
- **自检**：`python -m compileall -q endbox`、`python run_tests.py tests/test_account.py -q`（42 passed）、
  `python run_tests.py tests/test_foundation.py -q`（24 passed，含文案 key 存在性检查）、
  `python run_tests.py -q`（全量 272 passed）、`tools/shot.py account` 深/浅截图 + 演示模式 + 确认框截图，
  均人工核对无溢出。

