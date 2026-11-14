# 账号中心（模块一）

> 交付范围：`endbox/modules/account/**`、`endbox/ui/views/account.py`、`tests/test_account.py`、本文档。
> 契约见 `docs/CONTRACT.md` §7。本版把官服「Token 输入」改成了**森空岛 App 扫码登录**（B 服扫码保留）。
> **2026-11 扩展**：接入国服森空岛《终末地》`card/detail` 真实游戏数据——玩家卡、理智与满溢倒计时、
> 干员/武器/文档数量、干员列表（可排序筛选）、地区探索/通行证/每日任务摘要与可选签到入口；
> `state` 只新增字段、旧字段一个未删，旧界面/插件/HUD 行为不变。

## 1. 模块结构

| 文件 | 职责 |
| --- | --- |
| `endbox/modules/account/__init__.py` | 包入口，导出 `AccountService` 与 `create_service(ctx)` |
| `endbox/modules/account/models.py` | 契约状态结构、`Role` / `PlayerProfile` / `PlayerCard` / `Operator` 数据类、UID 脱敏、本地素材头像解析、演示数据 |
| `endbox/modules/account/skland.py` | 森空岛扫码：创建二维码 / 轮询 / 换 token / grant / cred / 签名请求 / 绑定解析 / **card/detail 完整数据解析** / **可选签到** / 凭据存储 |
| `endbox/modules/account/bilibili.py` | B 服扫码：generate / poll、Cookie 解析与 DPAPI 存储、B 站昵称头像 |
| `endbox/modules/account/qr.py` | 用 vendor 的 `QRCode` 生成 `list[list[bool]]` 矩阵给 `QRCanvas` |
| `endbox/modules/account/service.py` | `AccountService(QObject)`：异步刷新、多角色切换、签到、扫码状态机回调、事件广播、演示模式 |
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
    # 多角色 / 完整数据 / 签到（2026-11）：
    def select_role(self, index, *, on_done=None, on_error=None) -> None   # 持久化角色序号并重拉 card/detail
    def skland_sign_attendance(self, *, on_done=None, on_error=None) -> None  # 终末地签到（实验接口）
    # B 服扫码：
    def bili_generate(self, *, on_done=None, on_error=None) -> None
    def bili_poll(self, qrcode_key, *, on_done=None, on_error=None) -> None
    def commit_bili_login(self, cookies, *, on_done=None, on_error=None) -> None
    # 其他：
    def set_demo_mode(self, enabled: bool) -> None
    def has_credentials(self) -> bool
    def demo_enabled(self) -> bool
```

`state` / `snapshot()` 字段（必选齐全，缺失用 None / 0 / []）。
**旧字段保持原样**，`player / operators / role_index / data_error` 是 2026-11 新增：

```python
{
  "online": bool, "provider": "skland" | "bilibili" | "",
  "nickname": str | None, "uid_masked": str | None, "avatar_path": str | None,
  "roles": [{"name": str, "level": int | None, "server": str | None,
             "class": str | None, "element": str | None}],
  "last_sync": int, "demo": bool, "error": str | None,
  # -- 2026-11 新增（旧消费方可忽略）--
  "player": {                      # card/detail 整理后的玩家档案；拉取失败/未连接为 None
    "nickname": str | None, "uid_masked": str | None, "server_name": str | None,
    "level": int | None, "world_level": int | None, "exp": int | None,
    "avatar_path": str | None, "char_num": int | None, "weapon_num": int | None,
    "doc_num": int | None, "create_time": int | None, "last_login_time": int | None,
    "save_time": int | None, "main_mission": str | None,
    "stamina": {"cur": int | None, "max": int | None, "full_ts": int | None},
    "bp": {"cur": int | None, "max": int | None},
    "daily": {"cur": int | None, "max": int | None},
    "achieve_count": int | None,
    "domains": [{"name": str, "level": int | None, "settlements": int | None,
                 "settlement_max_level": int | None, "puzzle": int | None,
                 "chest": int | None, "piece": int | None, "blackbox": int | None}],
    "spaceship_rooms": [{"type": int | None, "level": int | None}],
  } | None,
  "operators": [{"name": str, "char_id": str | None, "rarity": int | None,
                 "rarity_key": str | None, "profession": str | None,
                 "element": str | None, "level": int | None,
                 "evolve_phase": int | None, "potential_level": int | None,
                 "weapon_name": str | None, "weapon_rarity": int | None,
                 "weapon_level": int | None, "avatar_url": str | None,
                 "avatar_path": str | None}],
  "role_index": int,               # 当前展示的终末地角色序号（多角色时用于切换）
  "data_error": str | None,        # 绑定成功但完整数据失败时的人话提示（不阻塞登录）
}
```

- `roles` 里的角色条目**仍只有契约的 5 个键**；`roleId / serverId` 只保存在模块内部的
  `Role` 对象里（调用 card/detail 用），不写进 state，避免完整 UID 外泄。
- 事件：每次状态变化都广播
  `account.state`（`nickname / online / avatar_path / provider / roles`）与
  `account.updated`（`snapshot=state` 深拷贝）。
- 启动/刷新时：有 `skland.credential` 就先读绑定列表 + 选中角色的 `card/detail`（cred 过期时
  静默刷新一次 token 再重试）；`card/detail` 失败只在 `data_error` 里降级提示，**绑定列表不丢**；
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

### 3.5 终末地真实游戏数据：card/detail（2026-11 新增）

> 接口事实（2026-11-14 实测，无真实账号）：`GET https://zonai.skland.com/api/v1/game/endfield/card/detail`
> 国服存在；不带凭据返回 `401 {"code":10002,"message":"用户未登录"}`。`GET .../web/v1/game/endfield/enums`
> 同样存在。**以下解析按公开文档结构（`_devdata/research/skport-readme.md`）实现并用真实结构 mock 覆盖，
> 没有真实账号做端到端验证。**

**请求**

```
GET https://zonai.skland.com/api/v1/game/endfield/card/detail?roleId={roleId}&serverId={serverId}
Headers: cred / platform / timestamp / dId / vName / sign
         sk-game-role: 3_{roleId}_{serverId}
签名: V2，签名文本 = path + query + timestamp + json(sign_headers)
     （GET 带 query 时 query string 必须逐字节参与签名，见 `skland._query_string`）
```

- `roleId` / `serverId` 来自绑定列表：`roleId`（缺省回退 `uid`）；
  `serverId` 依次读 `serverId` / `server_id` / `channelMasterId`（国服历史字段，待真实账号确认）。
  两者缺一就只显示绑定列表并给 `data_error` 人话提示，**不猜、不编造**。
- 绑定列表同时兼容国服 `bindingList[]{uid,nickName,channelName,channelMasterId}` 与国际服
  `bindingList[]{defaultRole:{roleId,serverId,nickname,serverName}, roles:[]}` 两种结构，按 id 去重。
- 多角色：`AccountService.select_role(index)` 把序号写入 `settings["account.role_index"]`
  （普通设置，不含凭据），重新拉 card/detail；`Role` 内部持有 roleId/serverId，不进 state。
- **缓存**：service 在内存里按 `(roleId, serverId)` 缓存最近一次**成功**的 card/detail
  （不落盘、不含凭据）。若下次绑定列表成功但完整数据暂时失败，会回退到缓存并在
  `data_error` / 界面上明确写「显示上次缓存，可能不是最新」；没有缓存则降级为绑定列表。
- 解析结果见 `state["player"] / state["operators"]` 字段表（§2）：
  `base{roleId,name,serverName,level,worldLevel,exp,avatarUrl,charNum,weaponNum,docNum,
  createTime,lastLoginTime,saveTime,mainMission.description}`；
  `dungeon{curStamina,maxStamina,maxTs}`；`bpSystem{curLevel,maxLevel}`；
  `dailyMission{dailyActivation,maxDailyActivation}`；`achieve.count`；
  `domain[]{name,level,settlements[],collections{puzzleCount,trchestCount,pieceCount,blackboxCount}}`；
  `spaceShip.rooms[]{type,level}`；`chars[]{charData{id,name,avatarSqUrl,rarity.key,profession.value,
  property.value}, level, evolvePhase, potentialLevel, weapon{weaponData{name,rarity},level}}`。
- **头像**：`base.avatarUrl` / `charData.avatarSqUrl` 指向静态 CDN；本版没有真实账号验证图片可访问性，
  因此**不下载远程图片**：干员头像优先用本地素材 `素材/operators/<干员名>/avatar.png`，
  缺图用名称首字占位；玩家头像用昵称首字占位。`avatar_url` 仍保留在 `state["operators"]` 里供后续扩展。

**降级与错误分支（界面可读中文）**

| 情况 | 行为 |
| --- | --- |
| card/detail HTTP 401 / `code=10002` / 未登录 | `CredentialExpiredError` → 静默刷新一次 cred 后重试；仍失败按登录失效提示 |
| `data_error`（其他错误码、格式异常、缺 base） | 保留绑定列表与 `online=True`，`player=None`，界面显示黄色说明，不阻塞刷新 |
| 绑定信息缺 roleId/serverId | `account.data.role_incomplete` 提示，绑定列表仍显示 |
| 网络失败 | `AccountError` 人话 + 重试建议；凭据保留 |
| 演示模式 | `demo_state()` 提供与 card/detail 同结构的演示数据，`demo=True` 明确标注 |

**可选签到（实验接口，未验证）**

`GET/POST https://zonai.skland.com/web/v1/game/endfield/attendance`，带 `sk-game-role` 头，
POST body `{}`（签名文本用 body 代替 query）。社区记录还需要 Cookie，但本工具只保存森空岛
cred/sign token、没有网页 Cookie，因此签到**大概率不可用**：UI 入口标注「实验接口，未经真实账号验证」，
失败只在通行证卡内显示原因并 Toast，不影响登录/刷新/其他模块。成功时按 `data.awards` 数量显示。

## 4. B 服（哔哩哔哩扫码，保留）

1. `GET https://passport.bilibili.com/x/passport-login/web/qrcode/generate` 拿 `data.url` + `data.qrcode_key`。
2. `qr.make_matrix(url)` → `QRCanvas.set_matrix()`。
3. 每 2s `GET .../web/qrcode/poll?qrcode_key=...`：`0` 成功 / `86101` 未扫码 / `86090` 已扫码待确认 / `86038` 过期。
4. 成功后从 `data.url` query 取 `SESSDATA`、`bili_jct`、`DedeUserID`、`DedeUserID__ckMd5`，
   分别以 `bili.*` 键经 DPAPI 存入 `secure_store`；再用 `nav` 接口取 `uname / mid / face`。
5. B 站身份不等于终末地角色数据：界面明确提示「角色数据仍需森空岛扫码」，不伪造角色。

轮询在离开视图 / 成功 / 过期 / 取消时停止；连续 5 次网络错误自动停止并提示刷新。

## 5. 界面

登录前是两栏扫码布局；森空岛登录成功（含演示模式）后切换为「状态带 + 真实游戏数据」布局；
**B 服登录后仍保留扫码布局**，方便继续扫森空岛拿终末地角色数据：

- **连接状态带**：`ON / OFF / DEMO` 编号块、来源、同步时间、角色数量、演示开关、刷新、
  唯一红色「退出登录」；在线时顶栏字段额外显示理智 `cur/max`（真实数据）。
- **玩家卡**：头像（无图用昵称首字）、昵称、脱敏 UID、服务器、来源、上次同步；
  大数字字段「权限等阶 / 探索等级 / 当前经验」；上次登录、苏醒日、当前任务；
  多角色时标题右侧出现角色切换分段器（`select_role`，序号持久化）。
- **理智卡**：当前理智大数字 + `/ 上限`、进度条、「满溢倒计时」（`maxTs` 真实时间戳，
  每秒刷新，到点显示「理智已满溢」）、恢复中/已满标签。
- **规模统计**：干员 / 武器 / 文档数量（`charNum / weaponNum / docNum`）。
- **干员列表**：表头「干员 / 潜能 / 精英化 / 等级 / 元素 / 职业 / 稀有度」；
  排序（等级 / 稀有度 / 潜能）与筛选（全部 / 6★ / 5★ / 4★及以下）分段器；
  每行头像（本地素材，缺图首字占位）、名称、`潜N`、`EN`、`Lv.N`、元素、职业、稀有度
  （6★ 用黄色填充块强调）。全部数字来自接口，缺失显示 `—`。
- **地区探索**：每个地区两行——名称 / 等级 / 据点数量与最高等级；谜质 / 储藏箱 / 维修灵感 /
  黑匣子计数（接口字段缺失显示 `—`）。
- **通行证与每日**：通行证等级与进度条、每日任务进度与进度条、光荣之路数量、
  「签到」按钮（实验接口，演示模式禁用并在按钮旁标注；失败只在卡内显示原因）。
- **未连接 / 未载入**：扫码卡 + 斜线纹空状态；`ctx.services.account is None` 时页面仍能打开，
  显示「账号模块未载入」并把扫码/刷新/演示按钮全部禁用。
- **演示模式**：状态块 `DEMO` + 黄色 InfoFlag「演示数据，非真实账号」；演示数据与 card/detail
  解析结果同结构（含理智/干员/探索/通行证），不触碰真实凭据，关闭后回到真实状态。
- **数据来源与安全**：说明第三方接口、card/detail 未端到端验证、DPAPI、轮询/过期策略。

视觉约束：容器直角 + 细线；只有按钮右下切角；黄色只做填充（6★/满溢/状态块）；
红色只在「退出登录」；没有圆角、投影、发光、渐变。二维码为保证可扫性固定黑白（其余部件走主题色）。

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
python tools/shot.py account shots/account_dark.png dark        # 未连接
python tools/shot.py account shots/account_light.png light
# 演示模式截图：本地临时数据目录（settings.json 里 account.demo_mode=true，不提交）：
#   python -c "import json,pathlib;p=pathlib.Path('_devdata_shot');p.mkdir(exist_ok=True);p.joinpath('settings.json').write_text(json.dumps({'account':{'demo_mode':True}}),'utf-8')"
ENDBOX_DATA_DIR=_devdata_shot python tools/shot.py account shots/account_data_dark.png dark
ENDBOX_DATA_DIR=_devdata_shot python tools/shot.py account shots/account_data_full_dark.png dark 1320 1900
```

测试不联网（68 个），覆盖：DPAPI 存储往返且明文不落盘、契约 state 字段与旧字段兼容、
UID 脱敏、设备 ID 稳定持久、扫码创建/状态/Token/授权码/cred 解析与错误文案、扫码状态四态与未知状态、
签名头算法（独立实现逐字节比对，含 card/detail 的 query 参与签名 + `sk-game-role` 头）、
cred 过期刷新重试、绑定列表过滤终末地 / 多角色 / `channelMasterId`→serverId /
国际服 `defaultRole+roles[]` 去重、B 站扫码状态与 Cookie、二维码矩阵、
card/detail 真实结构解析（base/dungeon/bpSystem/dailyMission/achieve/domain/spaceShip/chars）、
缺字段不编造、缺 base/错误码/401 分支、完整数据失败降级保留绑定列表 / 内存缓存回退并标注、
服务演示模式 / 真实数据刷新 / 多角色切换持久化 / 签到成功与无凭据失败、登出、
stop 后忽略迟到回调，以及视图层「退出登录必须先确认、取消时凭据与状态都不变、确认后才清除凭据、
服务缺失时能打开、旧 state 无新字段也能渲染、演示数据干员列表排序筛选」。

额外手工验证（真实网络 + 真窗口）：
- 森空岛生成二维码 + 轮询「未扫码」：`shots/account_skland_live.png`
- 隐藏视图后轮询停止、切回恢复；过期后自动刷新出新二维码（状态机断言）
- B 服二维码生成 + 轮询：`shots/account_bili_live.png`
- card/detail 无凭据请求真实返回 401 `{"code":10002,"message":"用户未登录"}`（仅验证端点存在）

## 8. 界面截图

- `shots/account_data_dark.png`、`shots/account_data_light.png`：**2026-11 新布局**（演示数据：
  玩家卡 / 理智倒计时 / 规模统计 / 干员列表 / 地区探索 / 通行证 / 签到入口）
- `shots/account_data_full_dark.png`：新布局完整长图（1320×1900）
- `shots/account_dark.png`、`shots/account_light.png`：未连接（默认）状态
- `shots/account_demo_dark.png`、`shots/account_demo_light.png`：旧版演示数据 + 角色列表（历史截图）
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
2. **card/detail 未端到端验证**：端点真实存在（无凭据 401 `code=10002`），但「有 cred 时返回什么」
   只能用公开文档结构 mock。字段名（`worldLevel / docNum / maxTs / dailyActivation / trchestCount` 等）
   若与国服实际返回不一致，会表现为全部/部分显示 `—`，不会崩、不会编造。
3. **roleId / serverId 来源**：国服绑定列表历史上用 `uid + channelMasterId + channelName`，
   国际服文档用 `defaultRole{roleId,serverId}`；两种都已兼容。若真实 payload 两者都没有 serverId，
   完整数据会降级为「绑定列表 + 提示」，需要真实账号再校准。
4. **设备验证**：未实现官方 `dId` 设备注册流程；若上游对 grant/cred 强制设备校验，
   会表现为「获取 cred 失败」，需要后续补充设备验证（目前失败提示明确、可重试）。
5. **头像**：无真实账号验证 `avatarUrl/avatarSqUrl` 可访问性，本版不下载远程图片；
   干员头像用本地 `素材/operators/<名称>/avatar.png`，缺失用首字占位；玩家头像用昵称首字。
6. **签到**：`web/v1/game/endfield/attendance` 是社区记录、未验证接口，且可能需要网页 Cookie；
   UI 标注「实验接口」，失败只提示不影响主流程。
7. **B 服**：只完成 B 站身份；终末地角色仍需森空岛扫码，界面与 `state["error"]` 均明确提示。
8. 部件库 `QRCanvas` 深色主题会反色，本视图用固定白底黑模块的子类规避；
   其他模块若直接用 `QRCanvas` 仍需注意。

## 11. 收尾与变更记录

### 11.1 2026-11 账户中心接真实数据（本次）

- **新增接口**：`skland.fetch_card_detail / parse_card_payload / parse_card_detail /
  fetch_player_data_with_refresh / fetch_attendance / sign_attendance / sign_attendance_for`；
  `build_signed_headers` 增加 `body` 参数（POST 用 body 参与签名，GET 行为不变）。
- **服务**：`AccountService.select_role(index)`（持久化 `account.role_index` 并重拉）、
  `skland_sign_attendance()`（异步签到）；`refresh()` 改为「绑定列表 + card/detail」，
  完整数据失败降级为 `data_error`（有内存缓存则回退并标注「可能不是最新」），登录态不丢。
  日志只记角色/干员数量，不落凭据。
- **模型**：新增 `PlayerCard`、`Operator`；`Role` 内部增加 `role_id/server_id`（不进 state）；
  `PlayerProfile` 增加 `card/data_error/role_index`；`state` 增加
  `player/operators/role_index/data_error`，旧字段全部保留。
- **视图**：登录前扫码布局、登录后「状态带 + 玩家卡 + 理智卡 + 规模统计 + 干员列表
  （排序/筛选/首字占位）+ 地区探索 + 通行证/每日/签到 + 说明」；理智满溢倒计时每秒刷新；
  服务缺失、旧 state、无完整数据三种降级路径都有测试。
- **文案**：新增 key 全部在 `assets/text/zh-CN.json` 的 `account` 节（`data / operators / stamina /
  scale / exploration / progress / sign / notes.card_detail / error.skland.card_* / attendance_*`
  等），用 edit 局部追加，未动其他 section。
- **自检**：`python -m compileall -q endbox` 通过；`python run_tests.py tests/test_account.py -q` → **68 passed**；
  `python run_tests.py tests/test_foundation.py -q` → 通过（含文案 key 存在性）；
  `tools/shot.py account` 深/浅 + 演示数据 + 满高截图人工核对无溢出；
  `python run_tests.py -q` → **433 passed**（2026-11-14 本次运行，团队并行收尾中数字会继续变化）。

### 11.2 2026-11-14 上一轮收尾

- **文案外部化**：账号视图与账号模块里所有用户可见中文（标题、标签、按钮、状态、空状态、
  错误与提示、tooltip、确认框文案）都集中在 `assets/text/zh-CN.json` 的 `account` 节，
  代码统一 `from endbox.core.text import t` 取值；占位符用 `{name}`。
- **删除/退出确认**：点「退出登录」先弹 `endbox.ui.kit.confirm(..., danger=True)`；取消则什么都不做，
  确认后才 `service.logout()` 清除森空岛凭据与 B 站 Cookie。账号页没有其他删除动作。
- **无旧 Token 输入 UI**：视图里没有 `QLineEdit` / token 输入卡，官服登录是森空岛 App 扫码。

