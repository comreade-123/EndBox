# -*- coding: utf-8 -*-
"""打包产物冒烟验证：启动 dist/EndBox.exe，确认装配完成、插件加载、数据目录落盘，然后退出。

    python tools/smoke_exe.py [exe路径]

用离屏平台启动（不弹窗、不干扰桌面），成功条件：
1. 进程在 60 秒内写出 `endbox.log`，且日志里出现服务装配 / 插件加载；
2. 数据目录被创建（便携模式）；
3. 进程存活到检查点（没有启动即崩）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "EndBox.exe"
TIMEOUT = 90.0


def main() -> int:
    if not EXE.is_file():
        print(f"FAIL: 找不到 {EXE}")
        return 1
    print(f"exe: {EXE} ({EXE.stat().st_size / 1024 / 1024:.1f} MB)")

    # 数据目录也放工作区：部分机器的 %TEMP% 对未知 exe 不允许写（本机就是）
    data_dir = Path(
        os.environ.get("ENDBOX_SMOKE_DATA") or (ROOT / "_devdata" / "exe_smoke_data")
    )
    shutil.rmtree(data_dir, ignore_errors=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    # 单文件 exe 会把运行库解压到 TMP。部分机器的 %TEMP% 不允许未知程序建目录
    # （本机就是），所以默认用工作区里的干净目录，可用 ENDBOX_SMOKE_TMP 覆盖。
    extract_dir = Path(
        os.environ.get("ENDBOX_SMOKE_TMP") or (ROOT / "_devdata" / "exe_smoke_tmp")
    )
    shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / "logs" / "endbox.log"
    env = dict(os.environ)
    env.update(
        {
            "ENDBOX_DATA_DIR": str(data_dir),
            "ENDBOX_NO_GLASS": "1",
            "ENDBOX_NO_UPDATE_CHECK": "1",
            "QT_QPA_PLATFORM": "offscreen",
            # 单文件 exe 会把运行库解压到 TMP：给一个干净目录，避免上次残留的 _MEI 目录干扰
            "TMP": str(extract_dir),
            "TEMP": str(extract_dir),
        }
    )
    started = time.perf_counter()
    process = subprocess.Popen(
        [str(EXE)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"pid={process.pid} data={data_dir}")

    log_text = ""
    ok = False
    while time.perf_counter() - started < TIMEOUT:
        if process.poll() is not None:
            print(f"FAIL: 进程提前退出，returncode={process.returncode}")
            break
        if log_path.is_file():
            log_text = log_path.read_text(encoding="utf-8", errors="replace")
            if "plugins loaded" in log_text and "service ready" in log_text:
                ok = True
                break
        time.sleep(0.5)
    else:
        print("FAIL: 超时未完成装配")

    try:
        process.terminate()
        process.wait(timeout=10)
    except Exception:  # noqa: BLE001
        process.kill()

    print(f"elapsed={time.perf_counter() - started:.1f}s log={log_path}")
    checks = {
        "data dir": data_dir.is_dir(),
        "log file": log_path.is_file(),
        "services ready": "service ready" in log_text,
        "plugins loaded": "plugins loaded" in log_text,
        "steps show": "run started" in log_text or "timeline service ready" in log_text,
    }
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    if log_text:
        tail = "\n".join(log_text.strip().splitlines()[-8:])
        print("--- log tail ---")
        print(tail)
    shutil.rmtree(data_dir, ignore_errors=True)
    return 0 if ok and all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
