# 排轴统计（timeline_stats）

EndBox 的示例插件：读取排轴快照，把真实数字算成统计看板。

## 功能

- **概览**：总时长、事件数、队伍人数、Buff 覆盖率（区间合并后真实计算，不是估算）；
- **按干员**：每名干员释放的技能 / 连携 / 终结技次数与合计；
- **按类型**：每类事件的数量、总时长、平均时长；
- 显示快照时间与最近一次 `timeline.state` 事件时间；
- **优雅降级**：排轴服务未载入（`ctx.services.timeline is None`）时显示「—」和原因，
  不编造数据；服务就绪后切回本页即可看到统计。

## 用到的插件 API

| API | 用途 |
| --- | --- |
| `"permissions": ["timeline:read"]` | manifest 声明读取排轴快照的权限，宿主才放行 |
| `ctx.timeline_snapshot()` | 只读快照；未授权会抛 `PermissionError` |
| `ctx.event_bus.subscribe("timeline.state", handler)` | 白名单事件订阅（`timeline.*` 允许） |
| `ctx.register_view(..., sidebar_icon="timeline", sidebar_label="排轴统计", order=20)` | 注册视图 |

## 数据结构

`ctx.timeline_snapshot()` 返回 `docs/CONTRACT.md §8` 里的结构：

```python
{
  "t": float, "playing": bool, "duration": float,
  "team": [{"name": ..., "class": ..., "element": ..., "avatar": ...}],
  "events": [{"operator": int, "kind": "skill"|"link"|"ult", "start": float, "duration": float}],
  "buffs": [{"name": str, "start": float, "end": float}],
}
```

服务缺失时返回空字典 `{}`，插件按空快照处理。
