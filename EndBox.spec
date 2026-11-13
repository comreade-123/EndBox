# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：EndBox 单文件 EXE。

构建：
    python -m PyInstaller --clean --noconfirm EndBox.spec

产物：
    dist/EndBox.exe   （单文件；首次运行在同目录创建 EndBox/ 数据文件夹）

说明：
  · 只打包运行所需资源（assets/ + plugins/），tests/tools/docs/参考项目/素材都不进包；
  · matplotlib 从 vendor/ 解析并随包携带（325 的 LaTeX 渲染）；
  · 应用图标使用 assets/branding/endbox.ico（透明背景，用户提供）；
  · 游戏素材 素材/ 不打包（版权 + 体积），放在 exe 同目录即可被排轴模块读取。
"""
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))  # 让 PyInstaller 分析到 vendored matplotlib

block_cipher = None

EXCLUDES = [
    # 开发/测试依赖（vendor 里给 run_tests 用，不进 exe）
    "pytest", "_pytest", "pluggy", "iniconfig", "colorama",
    # 未用到的 GUI/科学栈
    "tkinter", "_tkinter", "PyQt5", "PySide6", "PySide2",
    "IPython", "jupyter", "notebook", "pandas", "scipy", "sympy",
    "matplotlib.backends.backend_tkagg",
    "matplotlib.backends.backend_qt5agg",
    "matplotlib.backends.backend_webagg",
    "matplotlib.tests", "numpy.tests",
    # 参考项目/开发脚本（不是包，额外兜底）
    "tests", "tools",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT), str(ROOT / "vendor")],
    binaries=[],
    datas=[
        (str(ROOT / "assets"), "assets"),
        (str(ROOT / "plugins"), "plugins"),
    ],
    hiddenimports=[
        "matplotlib",
        "matplotlib.mathtext",
        "matplotlib.backends.backend_agg",
        "matplotlib.font_manager",
        "PIL.Image",
        "PIL.ImageFilter",
        "endbox.thirdparty.qrcode",
        "endbox.thirdparty.qrcode.main",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="EndBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "branding" / "endbox.ico"),
    version=str(ROOT / "tools" / "version_info.txt"),
)
