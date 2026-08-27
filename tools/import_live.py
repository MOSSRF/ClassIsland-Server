#!/usr/bin/env python3
"""
import_live.py — 把线上仓库的既有配置反向导入成 schedule.yaml

为什么需要它：
    线上仓库往往已有人工维护的真实作息（例如春季/秋季时间表
    31 项、周五版 19 项、周日 11 项）和 41 个在用课表。
    如果拿只含示例数据的 schedule.yaml 去生成并覆盖 timelayouts.json，
    这 41 个课表引用的 TimeLayoutId 会全部悬空 —— 静默损坏。
    正确做法是先把线上数据吃进 yaml，让流水线成为线上配置的超集。

关键设计：GUID 必须保持不变
    生成器平时用 uuid5 从名称派生 GUID（保证确定性）。但线上已有的
    时间表 GUID 是历史产物，派生不出来。所以导出的 yaml 里会带 `guid:`
    字段显式钉住原值，生成时优先用它。否则线上课表全部悬空。

用法：
    python3 tools/import_live.py --live <线上副本路径> -o schedule.live.yaml
    python3 tools/import_live.py --live <路径> --only 202509    # 只导某个 id
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import ci_schema as S  # noqa: E402

TT_NAME = {0: "class", 1: "break", 2: "divider", 3: "action"}
WD_KEY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}


def load_jsonc(p: Path) -> dict:
    """剥离字符串外的 // 与 /* */ 注释 + 尾随逗号。

    不能用 re.sub(r'//[^\n]*')：会把 URL 里的 https:// 一起削掉。
    """
    txt = p.read_text(encoding="utf-8")
    out, in_str, esc, i, n = [], False, False, 0, len(txt)
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
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def ts_to_hhmm(ts: str) -> str:
    """'08:45:00' → '08:45'；'1.08:00:00'（跨天）原样保留并告警。"""
    if not isinstance(ts, str):
        raise ValueError(f"时间不是字符串: {ts!r}")
    if "." in ts.split(":")[0]:
        return ts                      # 含天数，交给调用方处理
    parts = ts.split(":")
    if len(parts) < 2:
        raise ValueError(f"无法解析时间: {ts!r}")
    return f"{int(parts[0]):02d}:{parts[1]}"


def q(s: str) -> str:
    """YAML 标量：中文/含特殊字符时加引号。"""
    s = str(s)
    if s == "" or re.search(r'[:#\-{}\[\],&*?|>%@`"\']|^\s|\s$', s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", required=True, help="线上仓库本地副本路径")
    ap.add_argument("-o", "--out", default=str(ROOT / "schedule.live.yaml"))
    ap.add_argument("--only", action="append", help="只导入指定 id（可多次）")
    ap.add_argument("--include-broken", action="store_true",
                    help="连引用了不存在时间表的坏 id 也导出（默认跳过）")
    ap.add_argument("--base-url", default=None)
    a = ap.parse_args()

    live = Path(a.live)
    if not (live / "timelayouts.json").exists():
        print(f"✗ {live} 不像线上副本（缺 timelayouts.json）", file=sys.stderr)
        return 1

    tls = load_jsonc(live / "timelayouts.json").get("TimeLayouts") or {}
    subs = load_jsonc(live / "subjects.json").get("Subjects") or {}
    mf = load_jsonc(live / "manifest.json") if (live / "manifest.json").exists() else {}

    base = a.base_url
    if not base:
        v = ((mf.get("TimeLayoutSource") or {}).get("Value") or "")
        base = v.rsplit("/", 1)[0] if v else ""

    # ── 时间表：名称 → (guid, items, class槽数) ────────────────
    tl_by_guid: dict[str, dict] = {}
    name_count: dict[str, int] = {}
    warn: list[str] = []
    for gid, tl in tls.items():
        nm = tl.get("Name") or gid[:8]
        # 名称去重（线上可能有同名）
        if nm in name_count:
            name_count[nm] += 1
            nm = f"{nm}#{name_count[nm]}"
        else:
            name_count[nm] = 1
        items = tl.get("Layouts") or []
        n_slot = sum(1 for it in items if it.get("TimeType") == 0)
        tl_by_guid[gid] = {"name": nm, "items": items, "slots": n_slot}

    # ── 科目：guid → 名称 ─────────────────────────────────────
    sub_name: dict[str, str] = {}
    extra_subjects: dict[str, dict] = {}
    # load_official_subjects() 返回的是 {名称: GUID}，这里需要反向查
    official_guids: set[str] = set()
    try:
        official_guids = set((S.load_official_subjects() or {}).values())
    except Exception:
        pass
    for gid, s in subs.items():
        nm = s.get("Name") or gid[:8]
        sub_name[gid] = nm
        if gid not in official_guids:
            extra_subjects[nm] = {
                "guid": gid,
                "initial": s.get("Initial", ""),
                "teacher": s.get("TeacherName", ""),
                "outdoor": bool(s.get("IsOutDoor")),
            }

    # ── 班级 ──────────────────────────────────────────────────
    ids = []
    for sub in sorted(live.iterdir()):
        if not sub.is_dir() or sub.name == ".git":
            continue
        if (sub / "classplans.json").exists():
            if a.only and sub.name not in a.only:
                continue
            ids.append(sub.name)
    if not ids:
        print("✗ 没找到任何 <id>/classplans.json", file=sys.stderr)
        return 1

    L: list[str] = []
    L.append("# 由 tools/import_live.py 从线上仓库反向导入")
    L.append("# 线上既有 GUID 用 guid: 字段钉住，改动会导致客户端课表悬空，勿删")
    L.append("")
    L.append(f"school: {q(mf.get('OrganizationName', ''))}")
    L.append("")
    L.append("publish:")
    L.append(f"  base_url: {base}")
    L.append("")

    if mf.get("PolicySource") and (live / "policy.json").exists():
        pol = load_jsonc(live / "policy.json")
        L.append("policy:")
        for k, v in pol.items():
            L.append(f"  {k}: {str(bool(v)).lower()}")
        L.append("")

    L.append("timelayouts:")
    for gid, info in tl_by_guid.items():
        L.append(f"  {q(info['name'])}:")
        L.append(f"    guid: {gid}")
        L.append("    items:")
        for idx, it in enumerate(info["items"], 1):
            tt_raw = it.get("TimeType")
            tt = TT_NAME.get(tt_raw, "class")
            try:
                st = ts_to_hhmm(it.get("StartTime"))
                et = ts_to_hhmm(it.get("EndTime"))
            except ValueError as e:
                warn.append(f"时间表 {info['name']!r} 第{idx}项: {e}，已跳过")
                continue
            if "." in str(it.get("StartTime", "")).split(":")[0]:
                warn.append(f"时间表 {info['name']!r} 含跨天时间 {it.get('StartTime')!r}，已原样保留")
            # 分割线(2)/行动(3) 是零长度标记，官方写法就是 end==start，不输出 end
            zero_len = tt_raw in (2, 3)
            # 线上确实存在的脏数据：上课/课间却 end <= start
            # （实例：12:35 → 02:10，显然是 14:10 之误）
            # 既不静默修正也不静默丢弃，而是注释掉 + 列入告警，由人工定夺
            broken = (not zero_len) and et <= st
            row = f"      - {{ start: {q(st)}, "
            if not zero_len:
                row += f"end: {q(et)}, "
            row += f"type: {tt}"
            bn = it.get("BreakName") or ""
            if bn:
                row += f", name: {q(bn)}"
            row += " }"
            if broken:
                warn.append(
                    f"⚠ 线上脏数据：时间表 {info['name']!r} 第{idx}项 "
                    f"type={tt} 但 {st}→{et}（end 不晚于 start），"
                    f"已注释掉，需人工确认真实时间后启用")
                L.append("      # FIXME 线上原值非法（end ≤ start），请改正后启用：")
                L.append(f"      # {row.strip()}")
            else:
                L.append(row)
    L.append("")

    if extra_subjects:
        L.append("# 官方 21 科之外的科目（GUID 已钉住）")
        L.append("subjects:")
        for nm, meta in extra_subjects.items():
            bits = [f"guid: {meta['guid']}"]
            if meta["initial"]:
                bits.append(f"initial: {q(meta['initial'])}")
            if meta["teacher"]:
                bits.append(f"teacher: {q(meta['teacher'])}")
            if meta["outdoor"]:
                bits.append("outdoor: true")
            L.append(f"  {q(nm)}: {{ {', '.join(bits)} }}")
        L.append("")

    L.append("classes:")
    n_plans = 0
    for cid in ids:
        cps = load_jsonc(live / cid / "classplans.json").get("ClassPlans") or {}
        if not cps:
            warn.append(f"id={cid!r} 的 ClassPlans 为空，已跳过")
            continue
        # 该 id 用得最多的时间表作为默认
        tally: dict[str, int] = {}
        for v in cps.values():
            tally[v.get("TimeLayoutId")] = tally.get(v.get("TimeLayoutId"), 0) + 1
        main_tl = max(tally, key=tally.get)
        # 线上存在本来就坏的 id（HELLO/TEST 引用的时间表已不在
        # timelayouts.json 里）。这类 id 导出后无法重建，默认跳过，
        # 避免一个历史垃圾目录拖坠整个流水线。
        missing_tls = {v.get("TimeLayoutId") for v in cps.values()} - set(tl_by_guid)
        if missing_tls and not a.include_broken:
            warn.append(
                f"id={cid!r} 已跳过：引用了 {len(missing_tls)} 个不存在的时间表"
                f"（线上原本就是坏的），如需强行导出用 --include-broken")
            continue
        totals = {int((v.get("TimeRule") or {}).get("WeekCountDivTotal") or 2)
                  for v in cps.values()}
        weeks = max(totals) if totals else 2

        L.append(f"  - id: {q(cid)}")
        L.append(f"    name: {q(cid)}")
        if main_tl in tl_by_guid:
            L.append(f"    timelayout: {q(tl_by_guid[main_tl]['name'])}")
        else:
            warn.append(f"id={cid!r} 主时间表 {main_tl} 不在 timelayouts.json 中")
            L.append(f"    timelayout: {q('缺失-' + str(main_tl)[:8])}")
        if weeks != 2:
            L.append(f"    weeks: {weeks}")
        L.append("    schedule:")

        # 同一天可能有多个轮换课表
        for gid, v in sorted(cps.items(),
                             key=lambda x: ((x[1].get("TimeRule") or {}).get("WeekDay", 0),
                                            (x[1].get("TimeRule") or {}).get("WeekCountDiv", 0))):
            tr = v.get("TimeRule") or {}
            wd = int(tr.get("WeekDay") or 0)
            div = int(tr.get("WeekCountDiv") or 0)
            key = WD_KEY.get(wd, "mon") + (f"@{div}" if div else "")
            names = []
            for c in (v.get("Classes") or []):
                sid_ = c.get("SubjectId")
                if not sid_ or sid_ == S.EMPTY_GUID:
                    names.append("~")
                else:
                    nm = sub_name.get(sid_)
                    if nm is None:
                        warn.append(f"id={cid!r} {key}: SubjectId {sid_} 不在 subjects.json")
                        names.append("~")
                    else:
                        names.append(q(nm))
            day_tl = v.get("TimeLayoutId")
            arr = "[" + ", ".join(names) + "]"
            if day_tl != main_tl and day_tl in tl_by_guid:
                L.append(f"      {key}: {{ timelayout: {q(tl_by_guid[day_tl]['name'])}, "
                         f"classes: {arr} }}")
            else:
                L.append(f"      {key}: {arr}")
            n_plans += 1
        L.append("")

    Path(a.out).write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"✓ {a.out}")
    print(f"  时间表 {len(tl_by_guid)} | 科目 {len(subs)}"
          f"（非官方 {len(extra_subjects)}）| id {len(ids)} | 课表 {n_plans}")
    if warn:
        print(f"  ⚠ {len(warn)} 条提示：")
        for w in warn[:15]:
            print(f"    · {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
