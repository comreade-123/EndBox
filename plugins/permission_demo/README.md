# 权限演示（permission_demo）

EndBox 的示例插件：故意**不申请任何权限**，用来验证宿主真的会拒绝越权调用，
并把原因显示在界面上。

## 功能

- 点击「读排轴快照 timeline_snapshot()」或「读账号快照 account_snapshot()」；
- 宿主检查 `permission_demo` 的 `plugin.json` 里没有 `timeline:read` / `account:read`，
  抛出 `PermissionError`，并把拒绝原因写入 `%APPDATA%/EndBox/logs/endbox.log`；
- 视图把异常类型、完整原因、授权方式展示出来，主程序和其他插件不受影响；
- 切到本视图时会自动尝试一次 `timeline_snapshot()`，方便直接看到效果。

## 要拿到权限怎么做

在自己的 `plugin.json` 里显式声明（只能声明宿主白名单里的权限）：

```json
"permissions": ["timeline:read"]
```

然后到「插件管理」页点「重新加载」。

## 用到的插件 API

| API | 用途 |
| --- | --- |
| `ctx.timeline_snapshot()` | 未授权 → `PermissionError`（被本示例捕获并显示） |
| `ctx.account_snapshot()` | 同上 |
| `ctx.manifest.permissions` | 显示当前插件实际拥有的权限 |

这个插件没有声明任何权限，也不订阅事件；它本身就是「失败可隔离」的活演示。
