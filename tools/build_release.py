#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""build_release.py — 打包「独立集控服务器」发布物（不含任何私有数据）。

产出一个开箱即用的压缩包，别人解压后：
    ./start-webui.sh          （Linux/macOS）
    start-webui.bat           （Windows）
打开网页 → 首次使用向导 → 粘贴自己的公开仓库地址，即可开始用。

严格只打包「程序与示例」，绝不夹带本机私有内容：
    含：src/ tools/ docs/ examples/ reference/ + README/LICENSE/VERSION/
        requirements.txt + 启动脚本 + config.example.json
    不含：.git、__pycache__、config.json（本机路径）、schedule.yaml（真实课表）、
        dist*/、live-repo/、backups/、versions.json、任何 *.pyc

用法：
    python3 tools/build_release.py                 # 产物到 release/
    python3 tools/build_release.py --out /tmp/x.zip
    python3 tools/build_release.py --format tar.gz
"""
from __future__ import annotations

import argparse
import os
import io
import re
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (相对路径, 是否必需)
INCLUDE_FILES = [
    ("README.md", True),
    ("LICENSE", True),
    ("VERSION", True),
    ("requirements.txt", True),
    ("config.example.json", True),
    ("start-webui.sh", False),
    ("start-webui.bat", False),
]
INCLUDE_DIRS = ["src", "tools", "vendor", "docs", "examples", "reference"]

# 任何命中这些片段的路径都直接排除（双保险，防止私有数据泄漏进发布物）
FORBIDDEN_PARTS = {
    ".git", "__pycache__", "dist", "dist_live", "live-repo", "backups",
    "release",
}
FORBIDDEN_NAMES = {"config.json", "schedule.yaml", "versions.json",
                  "FEATURE-GAP.md"}
FORBIDDEN_SUFFIX = {".pyc", ".pyo"}


def _acceptable(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in FORBIDDEN_PARTS for p in parts):
        return False
    if Path(rel).name in FORBIDDEN_NAMES:
        return False
    if Path(rel).suffix in FORBIDDEN_SUFFIX:
        return False
    # 只放行 schedule 的示例副本
    if re.match(r"schedule[\w.-]*\.ya?ml$", Path(rel).name) and \
            parts[0] != "examples":
        return False
    return True


def collect() -> list[Path]:
    files: list[Path] = []
    for rel, required in INCLUDE_FILES:
        p = ROOT / rel
        if p.exists():
            files.append(p)
        elif required:
            raise SystemExit(f"✗ 缺少必需文件：{rel}")
    for d in INCLUDE_DIRS:
        base = ROOT / d
        if not base.exists():
            raise SystemExit(f"✗ 缺少必需目录：{d}/")
        for p in sorted(base.rglob("*")):
            if p.is_file():
                rel = p.relative_to(ROOT).as_posix()
                if _acceptable(rel):
                    files.append(p)
    # 去重并排序，保证产物确定
    uniq = sorted(set(files), key=lambda p: p.relative_to(ROOT).as_posix())
    return uniq


def _private_terms() -> list[str]:
    """本机/个人专属的脱敏词表，来自环境变量 CISRV_PRIVATE_TERMS（逗号分隔）。

    刻意不把任何人的姓名/仓库名硬编码进开源仓库——硬编码本身就是一次泄漏。
    通用可疑模式（绝对路径等）由下方正则单独检查，不依赖个人词表。
    """
    raw = os.environ.get("CISRV_PRIVATE_TERMS", "")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


# 通用可疑模式（与具体个人无关，任何人打包都该拦）：POSIX 绝对路径、Windows 盘符路径
_SUSPICIOUS_RES = [
    re.compile(r"/[a-z0-9_.-]+/[a-z0-9_.-]+/(?:apps|home|users|vol\d)/", re.I),
    re.compile(r"[a-z]:\\\\(?:users|programdata)\\\\", re.I),
]


def _safe_name(files: list[Path]) -> None:
    """发布前再扫一遍，防止误带私有标记或本机绝对路径。"""
    terms = _private_terms()
    blob_exts = {".py", ".html", ".sh", ".bat", ".md", ".json",
                 ".txt", ".yaml", ".yml"}
    for f in files:
        if f.suffix.lower() not in blob_exts:
            continue
        if f.name == "build_release.py":
            continue  # 本文件实现了扫描本身，不扫自己
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        low = txt.lower()
        hits = [t for t in terms if t in low]
        if hits:
            raise SystemExit(
                f"✗ {f.relative_to(ROOT)} 含 CISRV_PRIVATE_TERMS 命中项 {hits}，"
                f"拒绝打包；请先脱敏。")
        for rgx in _SUSPICIOUS_RES:
            m = rgx.search(txt)
            if m:
                raise SystemExit(
                    f"✗ {f.relative_to(ROOT)} 含本机绝对路径 {m.group(0)!r}，"
                    f"拒绝打包；请先脱敏。")


def main() -> int:
    ap = argparse.ArgumentParser(description="打包独立集控服务器发布物")
    ap.add_argument("--out", default=None, help="输出文件路径")
    ap.add_argument("--format", choices=["zip", "tar.gz"], default="zip")
    a = ap.parse_args()

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    top = f"classisland-server-{version}"
    files = collect()
    _safe_name(files)

    outdir = ROOT / "release"
    if a.out:
        out = Path(a.out)
    else:
        outdir.mkdir(exist_ok=True)
        ext = "zip" if a.format == "zip" else "tar.gz"
        out = outdir / f"{top}.{ext}"

    out.parent.mkdir(parents=True, exist_ok=True)
    if a.format == "zip":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in files:
                arc = f"{top}/" + f.relative_to(ROOT).as_posix()
                z.write(f, arc)
    else:
        with tarfile.open(out, "w:gz") as t:
            for f in files:
                arc = f"{top}/" + f.relative_to(ROOT).as_posix()
                t.add(f, arcname=arc)

    size = out.stat().st_size / 1024
    print(f"✓ 已打包：{out}  ({size:.0f} KB, {len(files)} 个文件)")
    print(f"  顶层目录：{top}/")
    print("  启动：解压后运行 start-webui.sh（Linux/macOS）或 start-webui.bat（Windows）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
