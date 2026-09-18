#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""vendor_bootstrap.py — 内置第三方依赖引导（让发布包开箱即用、免联网 pip）。

本项目唯一的第三方依赖是 PyYAML。发布包在 ``vendor/yaml`` 内置了它的
**纯 Python** 版本（跨平台，Windows/macOS/Linux 通用，无需编译），
因此目标机器只要装有 Python 3.9+ 就能直接运行，不必再 ``pip install``。

便携包还可在 ``runtime/`` 内置 **Python 运行时**与 **MinGit**（当前仅
Windows）：启动脚本负责选用内置解释器；本模块的 ``activate()`` 则把内置
git 的可执行目录加入 ``PATH``，使代码里裸用的 ``git``（clone/commit/push）
在未安装 Git 的机器上也能工作。

用法（各入口脚本在 ``import yaml`` **之前**调用一次）::

    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))  # 能找到本模块
    import vendor_bootstrap
    vendor_bootstrap.activate()
    import yaml

设计要点：
  * 若运行环境已安装 PyYAML，仍优先使用系统版（``vendor`` 仅作兜底，
    在有 C 扩展的机器上可享受 libyaml 加速）；
  * 同时把 vendor 目录写入 ``PYTHONPATH``，使 webui 用
    ``subprocess`` 调起的 build.py / split.py / preflight.py 子进程
    也能找到内置依赖；
  * 幂等：重复调用、重复路径、vendor 目录缺失（开发裁剪场景）都安全。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# src/ 的上一级即项目根，vendor/ 位于 <root>/vendor
ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"

# 内置 MinGit 的可执行目录（按平台）。当前只打包 Windows 版；其它平台即使
# 目录存在也一并支持，逻辑相同。这些目录存在于便携包的 runtime/git/ 下。
_GIT_BIN_REL = ("cmd",) if os.name == "nt" else ("bin",)
GIT_DIR = ROOT / "runtime" / "git"

_activated = False


def _vendor_on_syspath() -> bool:
    try:
        vp = str(VENDOR)
    except OSError:
        return False
    return any(
        os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(vp))
        for p in sys.path
    )


def activate() -> None:
    """把内置 vendor 目录接入当前进程及子进程的模块搜索路径。"""
    global _activated
    if _activated:
        return

    if VENDOR.is_dir() and not _vendor_on_syspath():
        # 追加到末尾：系统已装 PyYAML 时优先用系统版（可能带 C 加速）；
        # 未装时内置纯 Python 版兜底，仍然免安装、可离线运行。
        sys.path.append(str(VENDOR))

    if VENDOR.is_dir():
        # 让 subprocess 子进程也能 import 到内置 yaml。
        # 追加而非覆盖，保留用户/环境既有的 PYTHONPATH。
        existing = os.environ.get("PYTHONPATH", "")
        parts = [p for p in existing.split(os.pathsep) if p]
        vp = str(VENDOR)
        if vp not in parts:
            parts.append(vp)
            os.environ["PYTHONPATH"] = os.pathsep.join(parts)

    _activate_bundled_git()

    _force_utf8_output()

    _activated = True


def _force_utf8_output() -> None:
    """重定向到文件/管道时强制 stdout/stderr 用 UTF-8。

    中文 Windows 上重定向输出的默认编码是 GBK，CLI 打印 ✓/⚠/中文时会
    UnicodeEncodeError 直接崩在收尾的 print 上（真机抓到：导入已成功落盘，
    却在打印结果时 traceback）。控制台（tty）不动——Python 在 Windows
    控制台上走宽字符 API，本身能正常显示中文/emoji。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            enc = getattr(stream, "encoding", "") or ""
            if not stream.isatty() and enc.lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _activate_bundled_git() -> None:
    """把内置 MinGit 的可执行目录前置进 PATH。

    项目里所有 git 调用都裸用 ``git`` 并继承环境（见 appconfig.git_env），
    因此只要让 ``runtime/git/cmd``（Windows）或 ``runtime/git/bin``（POSIX）
    出现在 PATH 中，未安装 Git 的机器也能 clone/commit/push。前置而非追加，
    保证便携包自包含优先；系统装了 Git 时仅在包内缺失时才被用到。
    """
    git_bin = GIT_DIR.joinpath(*_GIT_BIN_REL)
    if not git_bin.is_dir():
        return
    exe_name = "git.exe" if os.name == "nt" else "git"
    if not (git_bin / exe_name).exists():
        return
    gp = str(git_bin)
    cur = os.environ.get("PATH", "")
    parts = cur.split(os.pathsep) if cur else []
    if gp not in parts:
        os.environ["PATH"] = gp + (os.pathsep + cur if cur else "")
