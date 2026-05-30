#!/usr/bin/env python3
"""
voice2txt 跨平台快捷入口（Windows / Linux / macOS 通用）

  python start.py          # 开关 WebUI（未运行则后台启动+开浏览器，已运行则停止）
  python start.py start    # 后台启动
  python start.py stop     # 停止
  python start.py open     # 只打开浏览器
  python start.py status   # 查看是否在运行
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WEBUI_MODULE = "voice2txt.webui"

COMMANDS = {
    "start": ["-d"],
    "toggle": ["-t"],
    "stop": ["--stop"],
    "open": ["--open"],
    "status": ["--status"],
}


def main() -> None:
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "toggle").lower()
    if cmd in ("-h", "--help", "help"):
        print(__doc__.strip())
        return
    extra = sys.argv[2:] if len(sys.argv) > 2 else []
    flags = COMMANDS.get(cmd)
    if flags is None:
        print(f"未知命令: {cmd}\n可用: {', '.join(COMMANDS)}")
        sys.exit(1)
    os.chdir(ROOT)
    os.execv(
        sys.executable,
        [sys.executable, "-m", WEBUI_MODULE, *flags, *extra],
    )


if __name__ == "__main__":
    main()
