#!/usr/bin/env python3
"""
preflight.py — 发布前安全闸门：把 dist/ 和线上仓库对撞，拦住破坏性变更。

背景（真实事故预演）：
    线上仓库可能已有人工维护的真实作息
    （春季/秋季时间表 31 项、周五版 19 项、周日 11 项）和 41 个在用课表。
    若直接用只含 1 个示例作息的 dist/timelayouts.json 覆盖上去，
    **41 个课表引用的 TimeLayoutId 会全部悬空** —— 客户端拉到的课表
    找不到时间表，属于典型的静默损坏。

本脚本在 push 之前检查：
    1. 悬空引用：线上（及本地）classplans 引用的 TimeLayoutId / SubjectId
       是否都还存在于将要发布的 timelayouts.json / subjects.json 中
    2. 覆盖影响：列出会被覆盖的文件，以及被覆盖后受影响的 id
    3. Version 单调性：manifest 里每个 source 的 Version 必须 >= 线上现值
       （客户端判定是 `>` 严格大于，回退等于永久失效）

退出码 0 = 安全，1 = 有阻断性问题。

用法：
    python3 tools/preflight.py                      # 自动 clone 线上做对比
    python3 tools/preflight.py --live /tmp/probe    # 用已有的线上副本
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import appconfig  # noqa: E402
# 商店版 git 的 --exec-path 指向了不存在的 /var/packages/git/...，
GIT_ENV = appconfig.git_env()


def load_jsonc(p: Path) -> dict:
    """线上文件里有 // 注释，标准 json 解析不了。

    不能直接 re.sub(r'//[^\n]*')：那会把 URL 里的 https:// 一起削掉
    （manifest.json 第一个 Value 就是 https://gitee.com/... 直接解析失败）。
    这里做一个最小状态机，只剥离**字符串外**的 // 行注释。
    """
    txt = p.read_text(encoding="utf-8")
    out = []
    in_str = False
    esc = False
    i = 0
    n = len(txt)
    while i < n:
        ch = txt[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and txt[i + 1] == "/":
            while i < n and txt[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and txt[i + 1] == "*":
            i += 2
            while i + 1 < n and not (txt[i] == "*" and txt[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    cleaned = "".join(out)
    # 容忍尾随逗号（手工编辑的 json 常见）
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return json.loads(cleaned)


def clone_live(url: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix="ci_live_"))
    r = subprocess.run(
        ["git", "clone", "-q", "--depth", "1", url, str(d)],
        env=GIT_ENV, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        print(f"✗ clone 失败: {r.stderr.strip()[:300]}", file=sys.stderr)
        sys.exit(1)
    return d


def collect_plans(root: Path) -> dict[str, dict]:
    """{id: ClassPlans}，扫描所有 <id>/classplans.json"""
    out = {}
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name == ".git":
            continue
        f = sub / "classplans.json"
        if f.exists():
            try:
                out[sub.name] = load_jsonc(f).get("ClassPlans") or {}
            except Exception as e:
                print(f"  ⚠ {sub.name}/classplans.json 解析失败: {e}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(ROOT / "dist"))
    ap.add_argument("--live", default=None, help="线上仓库本地副本；省略则自动 clone")
    ap.add_argument("--url", default=None, help="仓库地址，默认读 schedule.yaml")
    a = ap.parse_args()

    dist = Path(a.dist)
    if not (dist / "manifest.json").exists():
        print(f"✗ {dist} 里没有 manifest.json，先跑 src/split.py", file=sys.stderr)
        return 1

    url = a.url
    if not url:
        import yaml
        doc = yaml.safe_load((ROOT / "schedule.yaml").read_text(encoding="utf-8"))
        base = ((doc.get("publish") or {}).get("base_url") or "")
        m = re.match(r"(https://gitee\.com/[^/]+/[^/]+)/raw/", base)
        if not m:
            print(f"✗ 无法从 base_url 推出仓库地址: {base}", file=sys.stderr)
            return 1
        url = m.group(1)

    live = Path(a.live) if a.live else clone_live(url)
    print(f"线上: {url}\n本地: {dist}\n")

    problems: list[str] = []
    warnings: list[str] = []

    # ── 每班资源（v1.2.0 起 timelayouts/subjects 也按班下发） ────
    # manifest 里 TimeLayoutSource/SubjectsSource 现在带 {id}，
    # 所以每个班解析引用时只看自己目录下那两份文件。
    EMPTY = "00000000-0000-0000-0000-000000000000"

    def read_set(f: Path, key: str) -> set:
        if not f.exists():
            return set()
        try:
            return set(load_jsonc(f).get(key) or {})
        except Exception:
            return set()

    live_plans = collect_plans(live)
    dist_plans = collect_plans(dist)

    # 发布后每个 id 实际能解析到的资源集合。
    # 本次发布的班级 → 用新的每班文件；
    # 线上独有的班级 → 它们的 URL 也会变成 {id}/...，若该目录下没有这两份
    # 文件，发布后就会解析失败。这是 URL 结构变更特有的新风险，必须检出。
    def resolved(cid: str) -> tuple[set, set, bool]:
        d_tl = dist / cid / "timelayouts.json"
        d_sb = dist / cid / "subjects.json"
        if d_tl.exists() or d_sb.exists():
            return read_set(d_tl, "TimeLayouts"), read_set(d_sb, "Subjects"), True
        l_tl = live / cid / "timelayouts.json"
        l_sb = live / cid / "subjects.json"
        if l_tl.exists() or l_sb.exists():
            return read_set(l_tl, "TimeLayouts"), read_set(l_sb, "Subjects"), True
        return set(), set(), False        # 按班文件缺失 → 发布后取不到

    # 线上旧的根级共用文件（用于判断「本来就坏」）
    old_tl = read_set(live / "timelayouts.json", "TimeLayouts")
    old_sub = read_set(live / "subjects.json", "Subjects")

    print("── 悬空引用检查 ──")

    def dangling(cps: dict, tl_set: set, sub_set: set):
        bad_tl = {v.get("TimeLayoutId") for v in cps.values()} - tl_set
        bad_sub = set()
        for v in cps.values():
            for c in v.get("Classes") or []:
                s = c.get("SubjectId")
                if s and s != EMPTY:
                    bad_sub.add(s)
        return bad_tl, bad_sub - sub_set

    for scope, plans in (("线上", live_plans), ("本次", dist_plans)):
        for cid, cps in plans.items():
            if scope == "线上" and cid in dist_plans:
                continue          # 本次会整份替换，不算受害者
            tl_set, sub_set, have = resolved(cid)
            if not have:
                # 该 id 没有按班的 timelayouts/subjects：URL 带 {id} 后必然 404
                was_tl, was_sub = dangling(cps, old_tl, old_sub)
                # 已经坏掉的 id 不算「本次新引入」：
                #   · 引用了不存在的作息/科目 → 今天就取不到
                #   · 一个课表都没有         → 今天就是空白屏
                # 把这类历史垃圾判成阻断，预检就从安全网退化成死锁
                # （谁也没法发布，除非先去线上手工清理）。
                broken_before = bool(was_tl or was_sub) or not cps
                desc = (f"{scope} id={cid!r}（{len(cps)} 个课表）"
                        f"缺少 {cid}/timelayouts.json 与 {cid}/subjects.json")
                if broken_before:
                    warnings.append(desc + "（该 id 线上原本就已损坏）")
                    print(f"  ⚠ {desc}  ← 线上原本就坏")
                else:
                    problems.append(
                        desc + "；manifest 的 TimeLayoutSource/SubjectsSource "
                        "已带 {id}，发布后该班将取不到作息与科目")
                    print(f"  ✗ {desc}  ← 发布后会取不到！")
                continue
            bad_tl, bad_sub = dangling(cps, tl_set, sub_set)
            if not (bad_tl or bad_sub):
                print(f"  ✓ {scope} id={cid!r} 引用完整（按班自包含）")
                continue
            was_tl, was_sub = dangling(cps, old_tl, old_sub)
            pre_existing = bad_tl <= was_tl and bad_sub <= was_sub
            desc = (f"{scope} id={cid!r}（{len(cps)} 个课表）"
                    f"悬空 TimeLayoutId×{len(bad_tl)} SubjectId×{len(bad_sub)}")
            if pre_existing:
                warnings.append(desc + "（线上原本就已悬空，非本次引入）")
                print(f"  ⚠ {desc}  ← 线上原本就坏，不阻止发布")
            else:
                problems.append(desc + "（本次发布新引入）")
                print(f"  ✗ {desc}  ← 本次新引入！")
    if not live_plans:
        print("  （线上没有 classplans，跳过）")

    # ── 2. 覆盖影响 ────────────────────────────────────────────
    print("\n── 覆盖影响 ──")
    for f in sorted(p.relative_to(dist).as_posix()
                    for p in dist.rglob("*.json")):
        if f == "ManagementPreset.json":
            continue              # 不进仓库
        tgt = live / f
        if not tgt.exists():
            print(f"  + 新增 {f}")
        elif tgt.read_bytes() == (dist / f).read_bytes():
            print(f"  = 相同 {f}")
        else:
            print(f"  ~ 覆盖 {f}")

    only_live = sorted(set(live_plans) - set(dist_plans))
    if only_live:
        warnings.append(f"线上独有 id 不会被本次覆盖（保留）: {only_live}")

    # ── 3. Version 单调性 ──────────────────────────────────────
    print("\n── Version 单调性 ──")
    new_mf = json.loads((dist / "manifest.json").read_text("utf-8"))
    lf = live / "manifest.json"
    old_mf = load_jsonc(lf) if lf.exists() else {}
    for k, v in new_mf.items():
        if not isinstance(v, dict) or "Version" not in v:
            continue
        nv = int(v["Version"])
        ov = int((old_mf.get(k) or {}).get("Version", 0))
        if nv < ov:
            problems.append(f"{k} Version 回退 {ov} → {nv}（客户端判定是严格 >，回退=永久失效）")
            print(f"  ✗ {k}: {ov} → {nv} 回退！")
        elif nv == ov:
            same = (lf.exists() and (old_mf.get(k) or {}).get("Value") == v.get("Value"))
            note = "内容需确认未变" if same else "URL 变了但 Version 没涨 → 客户端不会更新"
            if not same:
                problems.append(f"{k}: URL 变更但 Version 仍为 {nv}")
            print(f"  {'✓' if same else '✗'} {k}: {ov} → {nv}（{note}）")
        else:
            print(f"  ✓ {k}: {ov} → {nv}")

    # ── 结论 ───────────────────────────────────────────────────
    print()
    for w in warnings:
        print(f"⚠ {w}")
    if problems:
        print(f"\n✗ 阻断（{len(problems)} 项），禁止发布：")
        for p in problems:
            print(f"    · {p}")
        print("\n  修法提示：若线上作息/科目仍在用，应先把线上数据导入 schedule.yaml，"
              "\n  让生成结果包含它们，而不是用示例数据覆盖。")
        return 1
    print("✓ 预检通过，可以发布")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
