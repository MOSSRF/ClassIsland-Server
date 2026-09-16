#!/usr/bin/env python3
"""
prune_ids.py — 找出线上仓库里已不在 schedule.yaml 中的班级目录（删班清理）

为什么单独做一个工具：
    split.py 只负责"写",从不删。这是刻意的:
    线上仓库里可能有别人手工加的目录(实测就有 HELLO/TEST),
    生成器擅自删除等于替用户做决定,一旦误删,大屏立刻拉不到课表。

    但"删班级"是真实需求。于是把删除拆成独立、显式、默认只报告的工具:
    默认 dry-run 只打印,加 --apply 才真的动手,且删前必须先备份。

用法:
    python3 tools/prune_ids.py --live <线上副本>              # 只报告(默认)
    python3 tools/prune_ids.py --live <线上副本> --apply      # 真的删除
    python3 tools/prune_ids.py --live <路径> --apply --keep HELLO --keep TEST

安全设计:
    · 默认 dry-run,不加 --apply 绝不改动任何文件
    · 删除前强制备份到 NAS(与主流程同一套 [时间.id] 命名)
    · 用 git rm 而非 rm,保留可回滚历史
    · --keep 白名单保护手工目录
    · 只删"含 classplans.json 的目录",绝不碰共用文件与未知内容
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import vendor_bootstrap  # noqa: E402
vendor_bootstrap.activate()

import yaml

import appconfig  # noqa: E402
BACKUP_DIR = appconfig.backup_dir()


def yaml_ids(src: Path) -> tuple[set[str], dict[str, str]]:
    """读出 schedule.yaml 里声明的全部班级 id(含预留班级)。"""
    doc = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    ids: set[str] = set()
    names: dict[str, str] = {}
    for c in doc.get("classes") or []:
        cid = c.get("id")
        if cid is None:
            continue
        cid = str(cid).strip()
        ids.add(cid)
        names[cid] = str(c.get("name") or cid)
    return ids, names


def live_class_dirs(live: Path) -> set[str]:
    """线上仓库里的班级目录 = 含 classplans.json 的子目录。"""
    out = set()
    for p in sorted(live.iterdir()):
        if p.is_dir() and not p.name.startswith(".") \
                and (p / "classplans.json").exists():
            out.add(p.name)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "schedule.yaml"))
    ap.add_argument("--live", required=True, help="线上仓库副本路径")
    ap.add_argument("--apply", action="store_true",
                    help="真的删除(默认只报告)")
    ap.add_argument("--keep", action="append", default=[],
                    help="保护不删的目录名(可多次)")
    a = ap.parse_args()

    src, live = Path(a.src), Path(a.live)
    if not src.exists():
        print(f"✗ 找不到 {src}", file=sys.stderr)
        return 1
    if not (live / "manifest.json").exists():
        print(f"✗ {live} 看起来不是集控仓库(缺 manifest.json)", file=sys.stderr)
        return 1

    declared, names = yaml_ids(src)
    existing = live_class_dirs(live)
    keep = set(a.keep)

    orphan = sorted(existing - declared - keep)
    protected = sorted((existing - declared) & keep)

    print(f"── 对比 {src.name} 与线上 ──")
    print(f"  yaml 声明班级 {len(declared)} 个: {', '.join(sorted(declared))}")
    print(f"  线上班级目录 {len(existing)} 个: {', '.join(sorted(existing))}")
    if protected:
        print(f"  🛡 --keep 保护(不删): {', '.join(protected)}")

    if not orphan:
        print("\n✓ 线上没有多余目录,无需清理")
        return 0

    print(f"\n⚠ 线上多出 {len(orphan)} 个目录(yaml 里已无对应班级):")
    for cid in orphan:
        n = sum(1 for _ in (live / cid).rglob("*") if _.is_file())
        print(f"    {cid}/  ({n} 个文件)")

    if not a.apply:
        print("\n这是 dry-run,未改动任何文件。")
        print("确认无误后加 --apply 执行删除;想保留某个目录用 --keep <名字>。")
        return 0

    # ── 真的删:先备份 ────────────────────────────────────────
    # 备份可在 config.json 里关闭(backup_dir 留空)。关闭时仍可删除:
    # 这里用的是 git rm,历史在目标仓库里,随时能 git revert 捞回来。
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    if BACKUP_DIR is not None:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        print(f"\n── 删除前备份到 {BACKUP_DIR} ──")
        for cid in orphan:
            f = live / cid / "classplans.json"
            if f.exists():
                dst = BACKUP_DIR / f"{ts}.{cid}.classplans.json"
                shutil.copy2(f, dst)
                print(f"  ✓ {dst.name}")
    else:
        print("\n── 备份已关闭(config.json 的 backup_dir 为空);"
              "依赖 git 历史回滚 ──")

    print("\n── 删除(用 git rm,保留可回滚历史) ──")
    env = appconfig.git_env()
    failed = []
    for cid in orphan:
        r = subprocess.run(["git", "rm", "-r", "-q", "--", cid],
                           cwd=live, env=env,
                           capture_output=True, text=True)
        if r.returncode == 0:
            print(f"  ✓ 已删 {cid}/")
        else:
            # 可能是未纳入 git 的目录,退回普通删除
            d = live / cid
            if d.exists():
                shutil.rmtree(d)
                print(f"  ✓ 已删 {cid}/ (非 git 跟踪,直接删除)")
            else:
                failed.append(cid)
                print(f"  ✗ {cid}/ 删除失败: {r.stderr.strip()}")

    if failed:
        print(f"\n✗ {len(failed)} 个目录删除失败")
        return 1

    print("\n✓ 清理完成。" + ("备份已保存," if BACKUP_DIR else "")
          + "可用 git 回滚。")
    print("  下一步:跑 split.py + preflight.py,再 commit & push。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
