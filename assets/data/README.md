# assets/data · 终末地数据快照

本目录只放**离线数据快照**，运行时不再联网。三个文件都由工具生成/维护，不要手改：

| 文件 | 内容 | 生成方式 |
| --- | --- | --- |
| `operators.json` | 全量干员 + 技能（角色/职业/属性/武器/阵营/词缀/简介/技能名与动画时长） | `python tools/fetch_game_data.py` |
| `game_meta.json` | 游戏版本、数据站 revision、来源声明、版权说明、素材目录约定 | 同上 |
| `operators_extra.json` | fz.wiki 补充资料（简介/词缀/阵营/CV 等），CC BY-NC-SA 4.0 | `python tools/build_operator_data.py`（历史快照，保留） |

## operators.json 结构

```jsonc
{
  "meta": {
    "schema": "endbox.operators/1",
    "game_version": "1.6.4",             // 游戏版本
    "revision": "1.6.4@11775769-12",     // 数据站 TableCfg 版本 id
    "operator_count": 36,                // 行数（管理员两个形态各一行）
    "unique_name_count": 34,             // 唯一干员名
    "skill_count": 180,
    "license": "…版权归鹰角，仅本地使用、不重新分发…",
    "field_notes": { … }                 // 每个字段的来源/口径说明
  },
  "operators": [
    {
      "id": "chr_0004_pelica",           // 游戏 CharacterTable 主键
      "name": "佩丽卡",                   // 中文名（I18nTextTable_CN 解析）
      "name_en": "Perlica",
      "rarity": 5,
      "profession": "术师",               // CharProfessionTable 枚举解析
      "profession_id": 5,
      "profession_icon": "icon_profession_5",
      "element": "电磁",                  // CharTypeTable 枚举解析
      "element_id": "Pulse",
      "element_color": "FFC000",
      "weapon": "施术单元",               // weaponType 映射（见下方局限）
      "weapon_id": 2,
      "faction": "终末地工业",             // fz.wiki 快照；无则 null
      "tags": ["电磁附着", "导电"],        // fz.wiki 快照；无则 []
      "bio": "…",                         // fz.wiki 快照；无则 null
      "department": "ENDFIELD INDUSTRIES",// 游戏表原始字段
      "variant": null,                    // 同名角色区分：管理员 "m"/"f"；无素材行为 null
      "has_assets": true,                 // 资产索引里是否有头像+立绘
      "avatar": "operators/佩丽卡/avatar.png",    // 「运行时素材根」相对路径
      "portrait": "operators/佩丽卡/portrait.png",
      "extra_key": "佩丽卡",               // 实际匹配到的 fz.wiki 快照键（排查用）
      "skills": [
        {
          "id": "chr_0004_pelica_normal_skill",
          "kind": "skill",                // attack / skill / link / ult / heavy
          "type": "战技",                  // heavy 为 null（游戏数据没有重击名）
          "name": "协议ω·雷击",
          "icon": "icon_skill_pelica_01", // 数据站 skillicon 目录文件名（无 .png）
          "duration": 5.167,              // SkillData.durationFrame / 30（秒）
          "duration_frame": 155,
          "desc": "…",                     // 已去掉游戏富文本标记
          "skill_ids": ["chr_0004_pelica_normal_skill"]
        }
      ]
    }
  ]
}
```

技能顺序固定为 普攻 → 战技 → 连携技 → 终结技 → 重击（重击只有 id/duration，其余字段
为 null，不编造）。渲染界面别忘了把 `kind` 映射到自己的文案 key。

### 素材读取约定

`avatar` / `portrait` 是**相对「运行时素材根」**的路径，不写死盘符：

1. 先找用户原素材 `素材/`（只读来源）；
2. 缺了再找 `assets/gamedata/`（`python tools/sync_game_data.py` 下载的本地缓存，gitignored）。

目录结构：

```
<素材根>/operators/<中文名>/avatar.png
<素材根>/operators/<中文名>/skills/{attack,skill,link,ult}.png
<素材根>/operators/<中文名>/class.txt、element.txt
<素材根>/classes/<职业>.png、<素材根>/elements/<属性>.png
```

## 已知局限（都写进了 meta.field_notes）

- **武器类型**：数据站没有武器类型枚举表，`weapon` 是 `weaponType` 与 fz.wiki 快照全量交叉
  验证得到的映射（1 单手剑 / 2 施术单元 / 3 双手剑 / 5 长柄武器 / 6 手铳）；遇到未收录值写 null。
- **阵营/词缀/简介**来自 fz.wiki 快照（CC BY-NC-SA 4.0），不是游戏内表；新角色快照没跟上时为 null。
- **重击（power_attack）**在游戏数据里没有名称/图标，`operators.json` 只给 id 与时长。
- **管理员**在 CharacterTable 里有 3 行（男/女/无素材的特殊行），界面按名取人时请用
  `variant` / `has_assets` 去重；本地素材里只有一个 `素材/operators/管理员/` 目录。
- **诀** 在游戏数据里只有一行；旧素材里是 `诀（意）` / `诀（智）` 两个目录。同步脚本会新建
  `gamedata/operators/诀/`，不覆盖旧素材。
- **职业名**：游戏数据是「术师」，旧素材 `素材/classes/` 里是「术士」；同步脚本会补
  `术师.png`，两套并存，读取方按素材根各自命中。

## 版权

游戏文本/图片版权归上海鹰角网络科技有限公司所有；数据由社区数据站
[data.akedata.wiki](https://data.akedata.wiki) 公开接口提供（AKEDatabase，前端 AGPL-3.0）；
fz.wiki 站内文本 CC BY-NC-SA 4.0。本目录与 `assets/gamedata/` 仅 EndBox 本地工具使用，
**不重新分发素材**。
