#!/usr/bin/env python3
"""
webui_bg.py — Windows 下把 webui.py 以后台无窗口方式拉起的引导器。

为什么需要它：
    用户希望双击 start-webui.bat 后「浏览器自动打开、黑色 cmd 窗口消失」，
    服务在后台继续跑。做法是用 pythonw.exe（无控制台解释器）以分离进程
    方式启动 webui.py：
      · 标准输出/错误重定向到 logs/webui.log（pythonw 没有控制台，不重定向
        的话启动失败会毫无提示，属于最难受的那类故障）；
      · 写 logs/webui.pid，stop-webui.bat 靠它找到后台进程；
      · 已在运行时再次双击：不重复起服务，直接打开浏览器。

本脚本本身在普通 python.exe 里短暂前台运行（由 bat 调用），
spawn 完后台进程后立即退出，bat 随之关闭窗口。

只依赖标准库。
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
PID_FILE = LOG_DIR / "webui.pid"
LOG_FILE = LOG_DIR / "webui.log"

# Windows 进程创建标志
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

WAIT_TIMEOUT = 258
SYNCHRONIZE = 0x00100000


def pid_alive(pid: int) -> bool:
    """进程是否还活着（不发信号、不打扰它）。"""
    if os.name == "nt":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(SYNCHRONIZE, False, pid)
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == WAIT_TIMEOUT
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid() -> int | None:
    try:
        return int(PID_FILE.read_text(encoding="ascii").strip())
    except Exception:
        return None


def http_alive(host: str, port: int) -> bool:
    """后台进程在跑不等于服务可用 —— 再探一下 HTTP。"""
    url = f"http://{'localhost' if host in ('0.0.0.0', '', '::') else host}:{port}/api/state"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            return 200 <= r.status < 500
    except Exception:
        return False


def rotate_log(log: Path, limit: int = 1_000_000) -> None:
    """日志超过约 1MB 就轮转一次，避免常年后台跑把日志涨到无限大。"""
    try:
        if log.exists() and log.stat().st_size > limit:
            old = log.with_suffix(".log.old")
            if old.exists():
                old.unlink()
            log.rename(old)
    except Exception:
        # 日志轮转永远不能挡住启动
        pass


def pythonw_path() -> str:
    """找无控制台解释器 pythonw；找不到（非 Windows / 精简安装）就退回当前解释器。"""
    if os.name == "nt":
        cand = Path(sys.executable).with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return sys.executable


def spawn(host: str, port: int) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rotate_log(LOG_FILE)
    logf = open(LOG_FILE, "ab")  # 子进程继承此句柄做 stdout/stderr
    cmd = [
        pythonw_path(),
        str(ROOT / "tools" / "webui.py"),
        "--host", host,
        "--port", str(port),
        "--pidfile", str(PID_FILE),
        "--open-browser",
    ]
    flags = 0
    if os.name == "nt":
        flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    env = dict(os.environ)
    # 重定向到文件后 stdout 是块缓冲，不关掉缓冲日志会迟迟不落地，
    # 后台进程报错时用户只能看到一个空日志，最难排查。
    env["PYTHONUNBUFFERED"] = "1"
    p = subprocess.Popen(
        cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=logf, stderr=subprocess.STDOUT,
        env=env,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
        close_fds=True,
    )
    return p.pid


def wait_ready(host: str, port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_alive(host, port):
            return True
        time.sleep(0.3)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    # 已在运行：核对 pid + HTTP，都在就只开浏览器，绝不重复起服务占端口
    old = read_pid()
    if old and pid_alive(old) and http_alive(args.host, args.port):
        print(f"服务已在运行（PID {old}），直接打开浏览器。")
        import webbrowser
        webbrowser.open(f"http://localhost:{args.port}")
        return 0
    if old and pid_alive(old) and not http_alive(args.host, args.port):
        print(f"[警告] 残留进程 PID {old} 还在但端口 {args.port} 无响应。")
        print(f"       先运行 stop-webui.bat 结束它，或查看日志：{LOG_FILE}")
        return 2

    pid = spawn(args.host, args.port)
    if wait_ready(args.host, args.port):
        print(f"✓ 后台服务已启动（PID {pid}），浏览器即将打开。")
        print(f"  地址：http://localhost:{args.port}")
        print(f"  日志：{LOG_FILE}")
        print("  停止服务：双击 stop-webui.bat")
        return 0

    print("[错误] 后台服务启动后未在限定时间内响应。")
    print(f"       请看日志定位原因：{LOG_FILE}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
