#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""build_portable.py — 打出「连 Python 都内置」的便携发布包（开箱即用）。

与 build_release.py 的纯源码包（约 350KB，要求机器自带 Python 3.9+）不同，
本脚本把一个**平台专用**的 Python 运行时塞进 ``runtime/python/``：

    classisland-server-<ver>-windows-x64.zip   （Windows 10/Server x64）
    classisland-server-<ver>-linux-x64.tar.gz  （glibc Linux x86_64，如这台 NAS）

用户解压后运行 start-webui.bat / start-webui.sh 即可，**无需安装 Python、
无需联网、无需管理员权限**。启动脚本优先使用内置解释器，找不到再回退系统
Python（见 start-webui.*），因此同一份脚本同时兼容源码包与便携包。

为什么按平台分包：
    CPython 是原生二进制，不存在“一份运行时处处可跑”。Windows / Linux 必须
    各带各的解释器；macOS 未内置（需要 Mac 构建并过公证），仍走系统 Python。

运行时来源（许可证均为 PSF License，与本项目 GPL-3.0 兼容；二进制**不进
git**，只在打包时从本地缓存注入）：
    · Windows：官方 embeddable zip，https://www.python.org/ftp/python/
    · Linux  ：astral-sh/python-build-standalone（可重定位、自带 OpenSSL），
               https://github.com/astral-sh/python-build-standalone

运行时缓存布局（均位于 gitignore 的 release/_runtime/ 下）：
    python-3.11.9-windows-x64-embed.zip
    cpython-3.11.16-linux-x64-standalone.tar.gz     上游原始归档
    python-windows-x64/  python-linux-x64/          展开（Linux 已裁剪）目录

用法：
    python3 tools/build_portable.py prepare   # 仅从归档展开/裁剪运行时
    python3 tools/build_portable.py           # 打两个便携包
    python3 tools/build_portable.py --only windows
    python3 tools/build_portable.py --only linux
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import build_release as BR  # noqa: E402  复用 collect()/脱敏扫描

RT_CACHE = ROOT / "release" / "_runtime"

# Windows 版额外内置的 MinGit（无头 Git，含 libcurl/OpenSSL/CA 证书）。
# vendor_bootstrap 会把 runtime/git/cmd 加入 PATH，使裸用的 git 可用。
BUNDLED_GIT = {
    "windows": {
        "archive": "MinGit-2.55.0.5-64-bit.zip",
        "dir": "git-windows-x64",
        "bin_rel": "cmd/git.exe",
        "version": "MinGit 2.55.0.5 (64-bit, Git for Windows)",
        "source": "https://github.com/git-for-windows/git/releases/"
                  "download/v2.55.0.windows.5/MinGit-2.55.0.5-64-bit.zip",
        "license": (
            "GPL-2.0-only（Git 本体）。作为独立程序与本项目聚合分发"
            "（经子进程调用，不链接、不合并代码）；全部上游许可证文件"
            "随包保留于本目录及 mingw64/share/licenses/。"
        ),
    },
}

# 运行时清单：版本升级时改这里，并把对应上游归档放进 release/_runtime/。
RUNTIMES = {
    "windows": {
        "archive": "python-3.11.9-windows-x64-embed.zip",
        "dir": "python-windows-x64",
        "ext": "zip",
        "py_rel": "python.exe",                 # runtime/python/python.exe
        "version": "CPython 3.11.9 (official embeddable, x86_64)",
        "source": "https://www.python.org/ftp/python/3.11.9/"
                  "python-3.11.9-embed-amd64.zip",
    },
    "linux": {
        "archive": "cpython-3.11.16-linux-x64-standalone.tar.gz",
        "dir": "python-linux-x64",
        "ext": "tar.gz",
        "py_rel": "bin/python3",                # runtime/python/bin/python3
        "version": "CPython 3.11.16 (python-build-standalone, glibc x86_64)",
        "source": "https://github.com/astral-sh/python-build-standalone/"
                  "releases/download/20260901/"
                  "cpython-3.11.16+20260901-x86_64-unknown-linux-gnu-"
                  "install_only.tar.gz",
    },
}

# Linux 运行时裁剪项：本项目只用一小部分标准库，删掉 GUI/打包/开发/测试件，
# 从 155MB 降到约 52MB。全部是确定用不到的；标准库 .py 一律保留以免误伤。
LINUX_DROP_DIRS = [
    "include", "share/man", "share/doc",
    "lib/python3.11/tkinter", "lib/python3.11/idlelib",
    "lib/python3.11/turtledemo", "lib/python3.11/lib2to3",
    "lib/python3.11/ensurepip", "lib/python3.11/test",
    "lib/python3.11/tests", "lib/python3.11/distutils",
    "lib/python3.11/site-packages/pip",
    "lib/python3.11/site-packages/setuptools",
    "lib/python3.11/site-packages/_distutils_hack",
]
LINUX_DROP_GLOBS = [
    "lib/python3.11/site-packages/pip-*.dist-info",
    "lib/python3.11/site-packages/setuptools-*.dist-info",
    "lib/python3.11/site-packages/distutils-precedence.pth",
    "lib/tcl*", "lib/tk*", "lib/itcl*", "lib/thread*",
    "lib/libtcl*.so*", "lib/libtk*.so*",
]
LINUX_DROP_BIN = [
    "2to3", "2to3-3.11", "idle3", "idle3.11", "pip", "pip3", "pip3.11",
    "pydoc3", "pydoc3.11", "python3-config", "python3.11-config",
]


def _sha256(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def prepare_windows(dest: Path, archive: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    if not (dest / "python.exe").exists():
        raise SystemExit(f"✗ {archive.name} 解包后未找到 python.exe")


def prepare_linux(dest: Path, archive: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as t:
        t.extractall(tmp)
    # standalone install_only 归档固定多一层 python/
    root = tmp / "python"
    if not root.is_dir():
        subs = [p for p in tmp.iterdir() if p.is_dir()]
        if len(subs) != 1:
            raise SystemExit(f"✗ {archive.name} 目录结构异常")
        root = subs[0]
    shutil.move(str(root), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)

    for rel in LINUX_DROP_DIRS:
        shutil.rmtree(dest / rel, ignore_errors=True)
    import glob as _glob
    for pat in LINUX_DROP_GLOBS:
        for m in _glob.glob(str(dest / pat)):
            p = Path(m)
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    for name in LINUX_DROP_BIN:
        (dest / "bin" / name).unlink(missing_ok=True)
    for cache in dest.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for a in dest.rglob("*.a"):
        a.unlink(missing_ok=True)

    # 裁剪后 strip 掉调试符号（解释器 55MB → 约 20MB，功能不变）。
    # 仅 Linux 构建能 strip ELF；找不到 strip 就跳过（包会大一些，仍可运行）。
    strip = shutil.which("strip")
    if strip:
        import subprocess as _sp
        for rel in ("bin/python3.11", "lib/libpython3.11.so.1.0"):
            tgt = dest / rel
            if tgt.exists():
                _sp.run([strip, "--strip-unneeded", str(tgt)], check=False)

    if not (dest / "bin" / "python3.11").exists():
        raise SystemExit("✗ Linux 运行时裁剪后丢失 bin/python3.11")


def prepare(platform: str) -> Path:
    cfg = RUNTIMES[platform]
    archive = RT_CACHE / cfg["archive"]
    dest = RT_CACHE / cfg["dir"]
    if not archive.exists():
        raise SystemExit(
            f"✗ 缺少运行时归档：{archive}\n  请先从官方源下载（见脚本头部注释）"
            f"：\n  {cfg['source']}")
    print(f"· 准备 {platform} 运行时 ← {archive.name}")
    if platform == "windows":
        prepare_windows(dest, archive)
    else:
        prepare_linux(dest, archive)
    print(f"  ✓ 展开到 {dest.relative_to(ROOT)}")

    # Windows 顺带展开内置 MinGit（可选：归档缺失不阻断，打包时再提示）。
    gcfg = BUNDLED_GIT.get(platform)
    if gcfg:
        ga = RT_CACHE / gcfg["archive"]
        gd = RT_CACHE / gcfg["dir"]
        if ga.exists():
            print(f"· 准备内置 git ← {ga.name}")
            prepare_git_windows(gd, ga)
            print(f"  ✓ 展开到 {gd.relative_to(ROOT)}")
        else:
            print(f"· 未找到 {ga.name}，跳过内置 git")
    return dest


def runtime_readme(cfg: dict, sha: str) -> bytes:
    return (
        "# 内置 Python 运行时\n\n"
        f"- 版本：{cfg['version']}\n"
        f"- 来源：{cfg['source']}\n"
        f"- 上游归档 SHA-256：`{sha}`\n"
        "- 许可证：PSF License（Python Software Foundation License，"
        "与本项目 GPL-3.0 兼容）\n\n"
        "本目录由 tools/build_portable.py 在打包时注入，**不随 git 分发**。\n"
        "启动脚本（start-webui.*）优先调用这里的解释器；若你删除本目录，"
        "脚本会回退到系统 Python 3.9+（PyYAML 已在 ../vendor 内置）。\n"
    ).encode("utf-8")


def git_readme(cfg: dict, sha: str) -> bytes:
    return (
        "# 内置 Git（MinGit）\n\n"
        f"- 版本：{cfg['version']}\n"
        f"- 来源：{cfg['source']}\n"
        f"- 上游归档 SHA-256：`{sha}`\n"
        f"- 许可证：{cfg['license']}\n\n"
        "这是 Git for Windows 官方面向第三方应用内嵌发布的**无头精简版**，\n"
        "仅含 git 命令行、libcurl/OpenSSL 与 CA 根证书，不含 GUI/资源管理器\n"
        "集成。程序启动时会把本目录的 `cmd/` 加入 PATH，使首次使用向导的\n"
        "clone 与「发布到线上」在未安装 Git 的 Windows 机器上也能直接工作。\n\n"
        "若删除本目录，程序会回退到系统 PATH 中的 git。\n"
    ).encode("utf-8")


def prepare_git_windows(dest: Path, archive: Path) -> None:
    """展开 MinGit（不裁剪：二进制间相互依赖，手删易损坏 HTTPS clone）。"""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    if not (dest / "cmd" / "git.exe").exists():
        raise SystemExit(f"✗ {archive.name} 解包后未找到 cmd/git.exe")


def _add_exec_bit(mode: int) -> int:
    return mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def build(platform: str, files: list[Path], outdir: Path) -> Path:
    cfg = RUNTIMES[platform]
    rt = RT_CACHE / cfg["dir"]
    if not (rt.exists()):
        rt = prepare(platform)
    # 运行时关键文件自检
    if not (rt / cfg["py_rel"]).exists():
        raise SystemExit(f"✗ 运行时缺少 {cfg['py_rel']}，请重新 prepare")

    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    top = f"classisland-server-{version}"
    suffix = "zip" if cfg["ext"] == "zip" else "tar.gz"
    out = outdir / f"{top}-{platform}-x64.{suffix}"
    sha = _sha256(RT_CACHE / cfg["archive"])

    # Windows 还可内置 MinGit；其它平台暂不内置（一般自带 git）。
    git_cfg = BUNDLED_GIT.get(platform)
    git_rt = (RT_CACHE / git_cfg["dir"]) if git_cfg else None
    if git_rt is not None and not (git_rt / git_cfg["bin_rel"]).exists():
        print(f"· 未找到内置 git（{git_rt.name}），本包将不含 git；"
              f"需内置请先解包 {git_cfg['archive']}")
        git_rt = None

    # 运行时里不允许夹带本机缓存/对象文件
    def rt_ok(rel: str) -> bool:
        parts = Path(rel).parts
        return "__pycache__" not in parts and not rel.endswith((".pyc", ".pyo", ".a"))

    if platform == "windows":
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
            for f in files:
                z.write(f, f"{top}/" + f.relative_to(ROOT).as_posix())
            z.writestr(f"{top}/runtime/PYTHON-RUNTIME.md", runtime_readme(cfg, sha))
            for p in sorted(rt.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(rt).as_posix()
                if not rt_ok(rel):
                    continue
                z.write(p, f"{top}/runtime/python/" + rel)
            if git_rt is not None:
                gsha = _sha256(RT_CACHE / git_cfg["archive"])
                z.writestr(f"{top}/runtime/GIT-RUNTIME.md",
                           git_readme(git_cfg, gsha))
                for p in sorted(git_rt.rglob("*")):
                    if not p.is_file():
                        continue
                    rel = p.relative_to(git_rt).as_posix()
                    if not rt_ok(rel):
                        continue
                    z.write(p, f"{top}/runtime/git/" + rel)
    else:
        with tarfile.open(out, "w:gz") as t:
            def add_file(src: Path, arc: str, execbit: bool = False):
                info = t.gettarinfo(str(src), arcname=arc)
                if execbit:
                    info.mode = _add_exec_bit(info.mode)
                with src.open("rb") as fh:
                    t.addfile(info, fh)

            for f in files:
                arc = f"{top}/" + f.relative_to(ROOT).as_posix()
                # 启动脚本在 tar 里带可执行位，解压即可 ./ 运行
                add_file(f, arc, execbit=(f.name == "start-webui.sh"))
            data = runtime_readme(cfg, sha)
            info = tarfile.TarInfo(f"{top}/runtime/PYTHON-RUNTIME.md")
            info.size = len(data)
            t.addfile(info, __import__("io").BytesIO(data))
            for p in sorted(rt.rglob("*")):
                rel = p.relative_to(rt).as_posix()
                if not rt_ok(rel):
                    continue
                arc = f"{top}/runtime/python/" + rel
                # 符号链接必须按链接打包，否则会被当成普通文件重复塞入
                # （bin/python3 → python3.11 会多打一份 20MB 解释器）。
                if p.is_symlink():
                    li = tarfile.TarInfo(arc)
                    li.type = tarfile.SYMTYPE
                    li.linkname = os.readlink(p)
                    li.mode = 0o777
                    t.addfile(li)
                    continue
                if not p.is_file():
                    continue
                add_file(p, arc)

    mb = out.stat().st_size / 1024 / 1024
    print(f"✓ {platform:7} 便携包：{out.name}  ({mb:.1f} MB)")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="打内置 Python 的便携发布包")
    ap.add_argument("--only", choices=["windows", "linux"],
                    help="只打指定平台（默认两个都打）")
    ap.add_argument("--prepare", action="store_true",
                    help="只展开/裁剪运行时，不打包")
    a = ap.parse_args()

    plats = [a.only] if a.only else ["windows", "linux"]
    if a.prepare:
        for p in plats:
            prepare(p)
        return 0

    files = BR.collect()
    BR._safe_name(files)  # 与源码包同一套脱敏闸门
    outdir = ROOT / "release"
    outdir.mkdir(exist_ok=True)
    for p in plats:
        build(p, files, outdir)
    print("启动方式：解压后运行 start-webui.bat（Windows）或 "
          "./start-webui.sh（Linux），无需安装任何东西。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
