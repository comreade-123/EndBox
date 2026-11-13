# 第三方组件与素材说明

## 随程序分发的第三方代码 / 字体

### Saira（字体）
- 文件：`assets/fonts/Saira*.ttf`（由 `assets/fonts/Saira.ttf` 可变字体切出的静态实例）
- 版权：Copyright 2020 The Saira Project Authors
- 许可证：SIL Open Font License 1.1，全文见 `assets/fonts/Saira-OFL.txt`
- 修改：使用 fontTools instancer 在 `wdth` 125 / 112 / 100 与 `wght` 400–800 上切片，
  并重写 name 表使每个实例可作为独立字族加载。

### qrcode（Python 库）
- 目录：`endbox/thirdparty/qrcode/`（由 PyPI 包 qrcode 8.2 复制，import 改为相对导入）
- 版权：Copyright (c) 2011 Lincoln Loop / qrcode contributors
- 许可证：BSD License，全文见 `endbox/thirdparty/qrcode/LICENSE`
- 用途：生成 B 站扫码登录二维码的模块矩阵，仅使用纯 Python 部分。

#### 应用图标
- `icon.png`（以及由 `tools/make_icon.py` 生成的 `assets/branding/endbox.png` / `endbox.ico`）
  为用户提供的同人插画素材，版权归原作者所有；EndBox 仅按用户要求在应用内展示，不单独再分发。

## 开发期工具（不随程序分发）
- `fonttools`、`pytest`：仅存在于 `vendor/` 目录，供 `tools/make_fonts.py` 与 `run_tests.py` 使用。

## 游戏素材

`素材/`（`operators/`、`classes/`、`elements/`、`cards/`、`glyph/`、`gen/`）与 `窗口参考.png`
为用户提供的游戏相关素材，版权归原权利方所有。EndBox 只在本地读取这些文件用于排轴界面展示，
不修改、不复制、不重新分发。若对外分发程序，应移除该目录并让用户自行放置素材。

## 参考实现（只参考行为，不复制代码）

- `zmd-charge-main/`（GitHub: [Lenkmat/EndfieldCharge](https://github.com/Lenkmat/EndfieldCharge)，
  MIT License）：HUD 三段式动画与交互参考。EndBox HUD 以 PyQt6 重写，不包含其 C# 代码。
- `Calculate325-main/`（MIT License）：325 计算器的功能与交互设计参考（求解模式/随机 L1-L5/Zc 编码/
  历史收藏/LaTeX 等）。EndBox 是独立的 Python 实现，按 MIT 要求在此致谢。
- [终末地 Wiki（fz.wiki）](https://www.fz.wiki/)：干员职业/属性/稀有度/阵营等**事实性元数据**的
  离线快照来源（`assets/data/operators_extra.json`），站内文字内容版权归其社区所有（CC BY-NC-SA），
  EndBox 只保存必要的结构化事实字段，不分发其页面文本。

## 数学公式渲染

- matplotlib（含 mathtext 渲染）以 vendored 方式放在 `vendor/`，仅用于 325 计算器的 LaTeX 公式排版；
  许可证为 matplotlib License（BSD 兼容，PSF 衍生），附于其发行包元数据中。

## 设计语言

UI 设计语言参考 endfield-ui 设计规范（内部设计指南），变体为"夜班"，强调色 `#FFFA00`，
字体为 Saira + 系统中文黑体。窗口外壳（圆角毛玻璃、独立侧边栏面板）按用户要求参考鹰角官方启动器。
未使用游戏官方字体、标志、图标或截图作为 UI 资产。
