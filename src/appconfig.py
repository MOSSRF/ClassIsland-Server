#!/usr/bin/env python3
"""
appconfig.py — 环境相关配置的唯一来源

为什么需要它：
    这个项目最初长在一台特定的 NAS 上，代码里写死了备份目录、git 路径、
    线上仓库副本位置。要开源就必须把「环境」和「逻辑」分开，
    否则别人克隆下来第一步就撞到一堆本机专属的绝对路径。

优先级（后者覆盖前者）：
    1. 本文件的默认值（相对项目目录，开箱可用）
    2. 项目根目录的 config.json（不进 git，见 .gitignore）
    3. 环境变量 CISRV_*

配置项：
    live_repo   线上集控仓库的本地副本路径（发布目标）
    backup_dir  备份目录（保存 yaml / 覆盖前的线上文件）。
                置为空字符串 = 关闭备份。
                （备份只是本地保险，不是流水线的必要环节：真正的历史在
                 目标仓库的 git 里。所以它必须可关，否则就是把某台特定
                 机器的使用习惯强加给所有人。）
    git_exec_path
                仅在 git 的 --exec-path 不正确时才需要设置。
                典型场景：某些 NAS 套件版 git 把 exec-path 指到不存在的
                目录，导致 https 传输直接不可用。留空表示用系统默认。
    git_branch  推送分支，默认 master（Gitee 新库默认分支通常是 master）
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_DEFAULTS = {
    "live_repo": str(ROOT / "live-repo"),
    "backup_dir": str(ROOT / "backups"),
    "git_exec_path": "",
    "git_branch": "master",
}

_ENV_PREFIX = "CISRV_"
_cache: dict | None = None


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    cfg = dict(_DEFAULTS)

    f = ROOT / "config.json"
    if f.exists():
        try:
            user = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"✗ config.json 不是合法 JSON：{e}")
        unknown = set(user) - set(_DEFAULTS)
        if unknown:
            # 静默忽略拼错的键会让人查半天，直接报出来
            raise SystemExit(
                f"✗ config.json 含未知配置项: {sorted(unknown)}；"
                f"可用: {sorted(_DEFAULTS)}")
        for k, v in user.items():
            if v is None:
                continue
            # backup_dir 允许显式空串（= 关闭备份），其余空值视为「不覆盖默认」
            if v == "" and k != "backup_dir":
                continue
            cfg[k] = v

    for k in _DEFAULTS:
        v = os.environ.get(_ENV_PREFIX + k.upper())
        if v:
            cfg[k] = v

    _cache = cfg
    return cfg


def get(key: str) -> str:
    return _load()[key]


def live_repo() -> Path:
    return Path(get("live_repo")).expanduser()


def backup_dir() -> Path | None:
    """备份目录；返回 None 表示用户显式关闭了备份。

    调用方必须处理 None —— 直接 Path("") 会变成当前目录，
    把备份文件糊到项目根下，属于典型的静默错行为。
    """
    v = (get("backup_dir") or "").strip()
    return Path(v).expanduser() if v else None


def git_branch() -> str:
    return get("git_branch")


def git_env() -> dict:
    """给 subprocess 用的环境变量；仅在需要时注入 GIT_EXEC_PATH。"""
    env = dict(os.environ)
    p = get("git_exec_path")
    if p:
        env["GIT_EXEC_PATH"] = p
    return env


def describe() -> str:
    c = _load()
    return "\n".join(f"  {k:14} {c[k] or '(默认)'}" for k in sorted(c))
