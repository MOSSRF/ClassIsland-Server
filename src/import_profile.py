#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
import_profile.py — 把客户端导出的单个 Default.json（一份 Profile）反向
合并进 schedule.yaml，并指定它归属的班级 id。

典型场景：老师在 ClassIsland 客户端里排好了一个班的课表，导出
Default.json 给管理员；管理员用本模块指定班级 id（如 "202506"）导入，
网页/流水线即可继续编辑、拆分、发布，不必在网页里重排一遍。

与 import_live.py 的区别：
    import_live.py 读的是**线上仓库文件树**（manifest + 各班目录里拆好的
    classplans/timelayouts/subjects），一次导全校；
    本模块读的是**客户端导出的一整份 Profile**（三个集合都在一个 JSON 里，
    全部课表只属于一个班），一次导一个班，班级 id 由调用方指定。

铁律（与全项目一致）：
    * 既有 GUID 一律钉住（timelayout/自定义科目写 guid:），改动会让线上
      客户端正在引用的课表/作息瞬间悬空 —— 静默损坏；
    * 官方 21 科按 GUID 识别成名字，不在 subjects: 里抄一份；
    * 合并后必须能过 build.py 的校验，否则拒绝写回；
    * 覆盖同名 id 班级是显式行为，调用方负责先备份（Web 端会备份原文）。
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import ci_schema as S

# TimeType 0=上课 1=课间 2=分割线 3=行动
TT_NAME = {0: "class", 1: "break", 2: "divider", 3: "action"}
# TimeRule.WeekDay: 0=周日..6=周六
WD_KEY = {0: "sun", 1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri", 6: "sat"}

_DUP_SUFFIX = "导入"


class ImportError_(ValueError):
    """导入输入本身不合法（区别于合并后构建校验失败）。"""


# ── 小工具 ────────────────────────────────────────────────────────

def timespan_to_hhmm(ts, warnings: list[str], ctx: str) -> str:
    """'07:30:00' → '07:30'。

    跨天（TimeSpan 带天数，形如 '1.08:00:00'）无法用 HH:MM 表达，
    保留原样并告警 —— build.py 会再次拦它，这里不静默改值。
    """
    if not isinstance(ts, str) or not ts:
        raise ImportError_(f"{ctx}: 时间值缺失或不是字符串: {ts!r}")
    head = ts.split(":", 1)[0]
    if "." in head:
        warnings.append(f"{ctx}: 含跨天时间 {ts!r}，已原样保留，需人工确认")
        return ts
    parts = ts.split(":")
    if len(parts) < 2:
        raise ImportError_(f"{ctx}: 无法解析时间 {ts!r}")
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        raise ImportError_(f"{ctx}: 时间不是数字 {ts!r}") from None
    return f"{h:02d}:{m:02d}"


def guess_class_name(plans: list[dict], fallback: str) -> str:
    """从课表名 '<班名> 周X[n]' 反推班级显示名。

    全部课表去掉星期后缀后得到唯一名字才采用，否则用调用方给的 fallback
    （通常就是班级 id），避免在多份混编档案里猜错。
    """
    names = set()
    for p in plans:
        nm = (p.get("Name") or "").strip()
        if not nm:
            continue
        # 去掉结尾的「 周一/周二2/周日1」等
        base = re.sub(r"\s*周[一二三四五六日]\d*\s*$", "", nm).strip()
        if base:
            names.add(base)
    if len(names) == 1:
        return next(iter(names))
    return fallback


# ── 现有 yaml 里的 GUID 索引 ─────────────────────────────────────

def _tl_guid_of(name: str, node) -> str:
    if isinstance(node, dict) and node.get("guid"):
        return str(node["guid"]).lower()
    return S.sid("timelayout", str(name))


def _tl_items_sig(items: list[dict]) -> tuple:
    """作息条目的规范化签名，用于判断同名/同 GUID 作息内容是否一致。"""
    out = []
    for it in items or []:
        out.append((str(it.get("type", "class")), str(it.get("start", "")),
                    str(it.get("end", "")), str(it.get("name", ""))))
    return tuple(out)


def _existing_tl_index(doc: dict) -> dict[str, str]:
    """现有 schedule.yaml 的作息：{guid: 名称}。"""
    return {_tl_guid_of(name, node): str(name)
            for name, node in (doc.get("timelayouts") or {}).items()}


def _existing_tl_sig(doc: dict) -> dict[str, tuple]:
    """现有作息：{guid: 条目签名}，供内容冲突判断。"""
    out = {}
    for name, node in (doc.get("timelayouts") or {}).items():
        items = node.get("items") if isinstance(node, dict) else node
        out[_tl_guid_of(name, node)] = _tl_items_sig(items or [])
    return out


def _existing_subject_index(doc: dict) -> dict[str, str]:
    """现有 schedule.yaml 的科目：{guid: 名称}（含官方 21 科）。"""
    official = S.load_official_subjects()          # {名称: guid}
    idx = {g.lower(): n for n, g in official.items()}
    for name, meta in (doc.get("subjects") or {}).items():
        meta = meta or {}
        gid = meta.get("guid") or official.get(str(name)) \
            or S.sid("subject", str(name))
        idx[str(gid).lower()] = str(name)
    return idx


def _unique_name(want: str, used: set[str]) -> str:
    """名称撞了但 GUID 不同：加（导入）/（导入2）… 后缀，绝不静默顶替。"""
    if want not in used:
        return want
    cand = f"{want}（{_DUP_SUFFIX}）"
    n = 2
    while cand in used:
        cand = f"{want}（{_DUP_SUFFIX}{n}）"
        n += 1
    return cand


# ── 核心转换 ──────────────────────────────────────────────────────

def merge_profile(doc: dict, profile: dict, cid: str,
                  cname: str | None = None) -> dict:
    """把一份 Profile 合并进已解析的 schedule.yaml 文档（原地修改并返回）。

    doc    : yaml.safe_load(schedule.yaml)
    profile: Default.json 解析出来的 dict
    cid    : 这份档案归属的班级 id（= 线上目录名 / 客户端班级标识）
    cname  : 班级显示名，None 时从课表名反推，再退回 cid
    """
    warnings: list[str] = []

    if not isinstance(profile, dict):
        raise ImportError_("Default.json 顶层不是对象")
    prof_tls = profile.get("TimeLayouts") or {}
    prof_plans = profile.get("ClassPlans") or {}
    prof_subs = profile.get("Subjects") or {}
    if not isinstance(prof_tls, dict) or not isinstance(prof_plans, dict) \
            or not isinstance(prof_subs, dict):
        raise ImportError_(
            "Default.json 结构不对：缺 TimeLayouts/ClassPlans/Subjects 集合")

    cid = str(cid).strip()
    if not cid or "/" in cid or cid.startswith("."):
        raise ImportError_(f"非法班级 id: {cid!r}（非空、不得含 / 或 . 开头）")

    # Overlay/临时计划不属于常规周课表，跳过并告知（不静默丢）
    plans = []
    for gid, p in prof_plans.items():
        if not isinstance(p, dict):
            continue
        if p.get("IsOverlay"):
            warnings.append(f"课表 {p.get('Name') or gid[:8]} 是叠加课表"
                            f"（IsOverlay），已跳过")
            continue
        plans.append((gid, p))
    if not plans:
        raise ImportError_("这份 Default.json 里没有常规周课表（ClassPlans 为空"
                           "或全是叠加/临时课表），无法导入")

    cname = (cname or guess_class_name([p for _, p in plans], cid)).strip() or cid
    if " " in cname or "\u3000" in cname:
        raise ImportError_(
            f"班级名 {cname!r} 含空格（课表名靠空格分隔班名与星期，"
            f"会串班），请用 --name 指定一个不含空格的名字")

    doc.setdefault("timelayouts", {})
    doc.setdefault("subjects", {})
    doc.setdefault("classes", [])

    tl_idx = _existing_tl_index(doc)
    tl_sig = _existing_tl_sig(doc)
    sub_idx = _existing_subject_index(doc)
    used_tl_names = {str(n) for n in doc["timelayouts"]}
    used_sub_names = {str(n) for n in doc["subjects"]}
    official_names = set(S.load_official_subjects())
    official_guids = {g.lower() for g in S.load_official_subjects().values()}

    # 档案 GUID → 最终生效 GUID（同 GUID 内容冲突时会重映射到新 GUID）
    tl_guid_remap: dict[str, str] = {}
    sub_guid_remap: dict[str, str] = {}

    # ── 1) 作息：GUID → yaml 名称；新作息全部 guid 钉桩后并入 ──
    tl_name_by_guid: dict[str, str] = {}
    for raw_gid, tl in prof_tls.items():
        gid = str(raw_gid).lower()
        items = []
        for i, it in enumerate((tl or {}).get("Layouts") or [], 1):
            tt = int(it.get("TimeType", 0))
            tname = TT_NAME.get(tt, "class")
            nm0 = str((tl or {}).get("Name") or gid[:8]).strip()
            if tt not in TT_NAME:
                warnings.append(f"作息 {nm0} 第{i}项: 未知 TimeType={tt}，按上课处理")
            ctx = f"作息 {nm0} 第{i}项"
            st = timespan_to_hhmm(it.get("StartTime"), warnings, ctx)
            row = {"start": st, "type": tname}
            if tt in (0, 1):
                row["end"] = timespan_to_hhmm(it.get("EndTime"), warnings, ctx)
            bn = (it.get("BreakName") or "").strip()
            if tname == "break" and bn:
                row["name"] = bn
            items.append(row)
        if not items:
            warnings.append(f"作息 {str((tl or {}).get('Name') or gid[:8])!r} "
                            f"没有任何时间点，已跳过")
            continue

        want = str((tl or {}).get("Name") or gid[:8]).strip()
        if gid in tl_idx:
            # 同 GUID：内容一致才复用；不一致（典型：两份档案各自用名称派生出
            # 同一个 GUID，作息却不同）必须另发 GUID + 改名，否则课表节数对不上
            if tl_sig.get(gid) == _tl_items_sig(items):
                tl_name_by_guid[gid] = tl_idx[gid]
                continue
            new_gid = str(uuid.uuid4())
            nm = _unique_name(want, used_tl_names)
            warnings.append(
                f"作息 {want!r} 的 GUID 与现有作息相同但内容不一致，"
                f"导入的这份改名为 {nm!r} 并分配新 GUID，两者都保留")
            tl_guid_remap[gid] = new_gid
            gid = new_gid
        else:
            nm = _unique_name(want, used_tl_names)
            if nm != want:
                warnings.append(f"作息名 {want!r} 已存在但 GUID 不同，"
                                f"导入的这份改名为 {nm!r}")
        doc["timelayouts"][nm] = {"guid": gid, "items": items}
        used_tl_names.add(nm)
        tl_name_by_guid[gid] = nm

    # ── 2) 科目：官方科按 GUID 认名字；自定义科 guid 钉桩并入 ──
    sub_name_by_guid: dict[str, str] = {}
    new_custom: dict[str, dict] = {}
    for gid, s in prof_subs.items():
        gl = str(gid).lower()
        want = str((s or {}).get("Name") or gl[:8]).strip()
        # 官方科目永远按 GUID 认名字，不重映射（GUID 即官方契约）
        if gl in official_guids:
            sub_name_by_guid[gl] = sub_idx.get(gl, want)
            continue
        if gl in sub_idx:
            # 自定义科同 GUID 但名称/老师等不同 → 另发 GUID + 改名
            existing_name = sub_idx[gl]
            meta_existing = doc["subjects"].get(existing_name) or {}
            same = (str(meta_existing.get("teacher") or "")
                    == str(s.get("TeacherName") or "")) and \
                   (str(meta_existing.get("initial") or "")
                    == str(s.get("Initial") or ""))
            if same:
                sub_name_by_guid[gl] = existing_name
                continue
            new_gid = str(uuid.uuid4())
            nm = _unique_name(want, used_sub_names | set(new_custom))
            warnings.append(
                f"自定义科目 {want!r} 的 GUID 已被占用且内容不同，"
                f"导入的这份改名为 {nm!r} 并分配新 GUID")
            sub_guid_remap[gl] = new_gid
            gl = new_gid
        else:
            nm = _unique_name(want, used_sub_names | set(new_custom))
            if nm != want:
                warnings.append(f"科目名 {want!r} 已存在但 GUID 不同，"
                                f"导入的这份改名为 {nm!r}")
        meta = {
            "guid": gl,
            "initial": str(s.get("Initial") or ""),
            "teacher": str(s.get("TeacherName") or ""),
            "outdoor": bool(s.get("IsOutDoor", False)),
        }
        new_custom[nm] = meta
        used_sub_names.add(nm)
        sub_name_by_guid[gl] = nm
    for nm, meta in new_custom.items():
        doc["subjects"][nm] = {k: v for k, v in meta.items() if v not in ("", False)}

    # ── 3) 课表：按 (星期, 轮换周) 排成 schedule ──
    days: dict[tuple[int, int], dict] = {}
    multi_days: set[tuple[int, int]] = set()
    max_div_total = 2
    tl_tally: dict[str, int] = {}
    for _gid, p in plans:
        tr = p.get("TimeRule") or {}
        wd = int(tr.get("WeekDay", 0))
        div = int(tr.get("WeekCountDiv") or 0)
        total = int(tr.get("WeekCountDivTotal") or 2)
        max_div_total = max(max_div_total, total, div if div else 0)
        tlg = tl_guid_remap.get(
            str(p.get("TimeLayoutId") or "").lower(),
            str(p.get("TimeLayoutId") or "").lower())
        tl_nm = tl_name_by_guid.get(tlg)
        if tl_nm is None:
            warnings.append(
                f"课表 {p.get('Name') or _gid[:8]} 引用了档案里不存在的作息 "
                f"{tlg}，已跳过该天")
            continue
        tl_tally[tl_nm] = tl_tally.get(tl_nm, 0) + 1

        slots = []
        for ci, c in enumerate(p.get("Classes") or [], 1):
            if c.get("IsEnabled") is False:
                warnings.append(f"课表 {p.get('Name') or _gid[:8]} 第{ci}节"
                                f"被标记为停用（IsEnabled=false），按空课处理")
                slots.append(None)
                continue
            sg0 = str(c.get("SubjectId") or "").lower()
            sg = sub_guid_remap.get(sg0, sg0)
            if not sg or sg == S.EMPTY_GUID:
                slots.append(None)
                continue
            nm = sub_name_by_guid.get(sg)
            if nm is None:
                warnings.append(f"课表 {p.get('Name') or _gid[:8]} 第{ci}节: "
                                f"SubjectId {sg} 不在科目表里，按空课处理")
                slots.append(None)
            else:
                slots.append(nm)
        if (wd, div) in days:
            multi_days.add((wd, div))
        days[(wd, div)] = {"tl": tl_nm, "classes": slots}

    if multi_days:
        labels = sorted(f"{S.WEEKDAY_CN.get(wd, wd)}{'第' + str(div) + '周' if div else ''}"
                        for wd, div in multi_days)
        warnings.append(
            "以下日子在档案里有多张课表（同一天只保留最后一张）："
            + "、".join(labels)
            + "。如果这份 Default.json 是把多个班的课表合在一起导出的，"
              "请改用单个班/单台机器导出的档案。")

    if not days:
        raise ImportError_("所有课表都引用了缺失的作息，没有可导入的天数")

    default_tl = max(tl_tally, key=tl_tally.get)
    schedule: dict = {}
    for (wd, div), day in sorted(days.items(), key=lambda x: (x[0][0], x[0][1])):
        key = WD_KEY.get(wd, "mon") + (f"@{div}" if div else "")
        if day["tl"] == default_tl:
            schedule[key] = day["classes"]
        else:
            schedule[key] = {"timelayout": day["tl"], "classes": day["classes"]}

    # ── 4) 任课老师：官方科目在这份档案里带老师名 → 班级级覆盖 ──
    # （自定义科目的老师已随全局 subjects 走；官方科不能改全局定义，
    #  否则等于把某个班的老师下发给全校。）
    overrides: dict[str, dict] = {}
    for gid, s in prof_subs.items():
        gl = str(gid).lower()
        nm = sub_name_by_guid.get(gl)
        if nm is None or nm not in official_names:
            continue
        t = str((s or {}).get("TeacherName") or "").strip()
        if t:
            overrides[nm] = {"teacher": t}

    class_spec = {
        "id": cid,
        "name": cname,
        "timelayout": default_tl,
        "schedule": schedule,
    }
    if max_div_total != 2:
        class_spec["weeks"] = max(2, max_div_total)
    if overrides:
        class_spec["subjects"] = overrides

    # ── 5) 替换或追加班级 ──
    replaced = False
    for i, c in enumerate(doc["classes"]):
        if str(c.get("id")) == cid:
            # 保留原班级上可能存在而导入数据没有的东西：这里没有可保留的，
            # schedule 来自整份档案是权威的。但 reserved 必须摘掉。
            doc["classes"][i] = class_spec
            replaced = True
            break
    if not replaced:
        doc["classes"].append(class_spec)

    return {"doc": doc, "warnings": warnings, "replaced": replaced,
            "class": class_spec}


def load_profile(path: str | Path) -> dict:
    raw = Path(path).read_text(encoding="utf-8-sig")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ImportError_(f"Default.json 不是合法 JSON：{e}") from None
