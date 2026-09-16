#!/usr/bin/env python3
"""
build.py — schedule.yaml → Default.json (完整 Profile，可直接导入客户端验证)

用法：
    python3 src/build.py [schedule.yaml] [-o dist/Default.json]

设计要点：
  * 确定性：同输入 → 字节一致输出（GUID 用 uuid5，无随机、无时间戳）
  * 快失败：科目名拼错、课程数与时间点数不匹配 → 立即报错并指出位置
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vendor_bootstrap  # noqa: E402
vendor_bootstrap.activate()

import yaml

import ci_schema as S


TYPE_MAP = {
    "class": S.TT_CLASS,
    "break": S.TT_BREAK,
    "divider": S.TT_DIVIDER,
    "action": S.TT_ACTION,
}


class BuildError(Exception):
    pass


def iter_day_classes(spec):
    """取出某一天的科目列表。

    schedule 的某一天有两种写法：
      1. list           —— 用班级默认作息
      2. dict           —— {timelayout: 覆盖作息, classes: [...]}
    这里只负责把科目名摘出来，供严格科目校验收集 used 使用；
    不能直接遍历 dict，否则会把 'timelayout'/'classes' 这两个键当成科目名。
    """
    if spec is None:
        return []
    if isinstance(spec, dict):
        return spec.get("classes") or []
    return spec


def build_timelayouts(spec: dict, warnings: list | None = None) -> tuple[dict, dict]:
    """返回 (TimeLayouts dict, {名称: (guid, 上课节数)})

    两种写法：
      1. 名称: [ {时间点}, ... ]                    —— GUID 由名称 uuid5 派生
      2. 名称: { guid: <既有GUID>, items: [ ... ] }   —— GUID 显式钉住

    第 2 种是对接线上历史数据的必需：线上已有时间表的 GUID 是历史产物，
    派生不出来。若不钉住而重新派生，线上所有课表引用的 TimeLayoutId
    会全部悬空 —— 客户端拉到课表却找不到时间表，属于静默损坏。
    """
    out, index = {}, {}
    seen_guid: dict[str, str] = {}
    if warnings is None:
        warnings = []
    for name, node in (spec or {}).items():
        pinned = None
        if isinstance(node, dict):
            items = node.get("items")
            pinned = node.get("guid")
            if items is None:
                raise BuildError(
                    f"时间表 {name!r} 用了 dict 形式但缺 items；"
                    f"应为 {{ guid: ..., items: [...] }}")
            if pinned is not None:
                pinned = str(pinned)
                if not re.fullmatch(
                        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
                        r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", pinned):
                    raise BuildError(
                        f"时间表 {name!r} 的 guid 不是合法 GUID: {pinned!r}")
                pinned = pinned.lower()
        else:
            items = node

        if not items:
            raise BuildError(f"时间表 {name!r} 是空的")
        layouts, n_class = [], 0
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                raise BuildError(
                    f"时间表 {name!r} 第 {i+1} 项应为映射，实际是 {type(it).__name__}：{it!r}")
            tname = str(it.get("type", "class"))
            if tname not in TYPE_MAP:
                raise BuildError(
                    f"时间表 {name!r} 第 {i+1} 项: 未知 type={tname!r}，"
                    f"应为 {'/'.join(TYPE_MAP)}")
            tt = TYPE_MAP[tname]
            start = it.get("start")
            if start is None:
                raise BuildError(f"时间表 {name!r} 第 {i+1} 项缺少 start")
            end = it.get("end", start)
            # 分割线(2)/行动(3) 是零长度标记，官方写法就是 end==start，不需 end
            zero_len = tt in (S.TT_DIVIDER, S.TT_ACTION)
            if not zero_len and it.get("end") is None:
                raise BuildError(
                    f"时间表 {name!r} 第 {i+1} 项 (type={tname}) 缺少 end")
            if zero_len:
                end = start
            try:
                item = S.time_layout_item(start, end, tt,
                                          break_name=it.get("name", "") or "")
            except ValueError as e:
                raise BuildError(f"时间表 {name!r} 第 {i+1} 项: {e}") from None
            # 时长校验：上课/课间的 end 必须严格晚于 start
            if not zero_len and item["EndTime"] <= item["StartTime"]:
                raise BuildError(
                    f"时间表 {name!r} 第 {i+1} 项: end({item['EndTime']}) "
                    f"未晚于 start({item['StartTime']})。"
                    f"若这是分割线/行动标记，请改 type: divider / action")
            layouts.append(item)
            if tt == S.TT_CLASS:
                n_class += 1
        # ── 时序校验：只管真正占据时间轴的项（上课/课间）──
        # 分割线(2)与行动(3) 官方就是 StartTime==EndTime 的零长度标记，
        # 且行动项可以出现在任意位置（实测线上 '秋季时间表' 第3项
        # 就是 13:10 的行动，插在 08:00 之前），所以均不参与递增校验。
        #
        # 重叠只告警不报错：线上真实作息（生产环境实例）存在
        # 午休课间 12:35-14:10 包含下午首节 13:20-14:00 的写法，
        # 客户端容忍这种嵌套。强行报错会阻止合法的存量数据入库。
        axis = [(i, x) for i, x in enumerate(layouts, 1)
                if x["TimeType"] in (S.TT_CLASS, S.TT_BREAK)]
        for (ia, a), (ib, b) in zip(axis, axis[1:]):
            if b["StartTime"] < a["EndTime"]:
                warnings.append(
                    f"时间表 {name!r} 第 {ia} 项与第 {ib} 项时间重叠: "
                    f"{a['StartTime']}-{a['EndTime']} 与 "
                    f"{b['StartTime']}-{b['EndTime']}")
        gid = pinned or S.sid("timelayout", name)
        if gid in seen_guid:
            raise BuildError(
                f"时间表 GUID 冲突: {name!r} 与 {seen_guid[gid]!r} 都是 {gid}")
        seen_guid[gid] = name
        out[gid] = S.time_layout(name, layouts)
        index[name] = (gid, n_class)
    return out, index


def build_subjects(spec: dict, used: set[str]) -> tuple[dict, dict]:
    """官方 21 科复用官方 GUID；其余用 uuid5 派生。

    ⚠️ 严格模式：schedule 里用到的科目名必须是官方 21 科之一，或在
    `subjects:` 里显式声明。否则拼错一个字就会静默生成一个幽灵科目
    并下发到全校大屏——这类错误不报错、只是错，最难查。
    """
    official = S.load_official_subjects()
    spec = spec or {}
    known = set(official) | set(spec)
    unknown = sorted(used - known)
    if unknown:
        lines = []
        for n in unknown:
            hint = difflib.get_close_matches(n, sorted(known), n=3, cutoff=0.4)
            tip = f"  （你是想写 {' / '.join(hint)} 吗？）" if hint else ""
            lines.append(f"    · {n!r}{tip}")
        raise BuildError(
            "schedule 里出现了未知科目：\n" + "\n".join(lines)
            + "\n  若确实要新增科目，请在 subjects: 里显式声明它。")

    out, index = {}, {}
    seen_guid: dict[str, str] = {}
    for name in sorted(known):
        meta = spec.get(name) or {}
        # guid: 显式钉住优先（对接线上历史科目），其次官方 21 科，最后才派生
        pinned = meta.get("guid")
        if pinned is not None:
            pinned = str(pinned).lower()
            if not re.fullmatch(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}"
                    r"-[0-9a-f]{4}-[0-9a-f]{12}", pinned):
                raise BuildError(f"科目 {name!r} 的 guid 不是合法 GUID: {pinned!r}")
        gid = pinned or official.get(name) or S.sid("subject", name)
        if gid in seen_guid:
            raise BuildError(
                f"科目 GUID 冲突: {name!r} 与 {seen_guid[gid]!r} 都是 {gid}")
        seen_guid[gid] = name
        out[gid] = S.subject(
            name,
            initial=meta.get("initial", ""),
            teacher=meta.get("teacher", ""),
            outdoor=bool(meta.get("outdoor", False)),
            guid=gid,
        )
        index[name] = gid
    return out, index


def build(doc: dict, warnings: list | None = None,
          plan_owner: dict | None = None,
          class_subjects: dict | None = None,
          class_timelayouts: dict | None = None) -> dict:
    """构建完整 Profile。

    plan_owner：可选出参，回填 {课表GUID: 班级id}。
    split.py 靠它把课表分到各班目录，避免用班级名做前缀匹配
    （名字重名或含空格时前缀匹配会串班，是静默错输出）。

    class_subjects：可选出参，回填 {班级id: {科目GUID: 科目记录}}。
    每班一份独立科目表的数据源。班级可用 `subjects:` 段覆盖老师名等
    字段，覆盖只作用于本班 —— 这正是「按班独立」与旧版全校共用的区别。

    class_timelayouts：可选出参，回填 {班级id: {作息GUID: 作息记录}}。
    只包含该班真正引用到的作息（班级默认 + 各天覆盖），
    避免把全校 5 套作息都下发给每个班。

    ⚠️ 科目/作息 GUID 在各班之间保持一致（不按班派生）。
    各班只加载自己那份文件，GUID 不冲突；而保持一致可以让
    既有 classplans.json 的 SubjectId / TimeLayoutId 继续有效。
    按班派生新 GUID 会让线上所有课表的引用瞬间悬空 —— 静默损坏。
    """
    if warnings is None:
        warnings = []
    if plan_owner is None:
        plan_owner = {}
    if class_subjects is None:
        class_subjects = {}
    if class_timelayouts is None:
        class_timelayouts = {}
    tls, tl_index = build_timelayouts(doc.get("timelayouts"), warnings)

    classes_spec = doc.get("classes") or []
    if not classes_spec:
        raise BuildError("classes 为空，没有任何班级")

    # 收集实际用到的科目名
    used: set[str] = set()
    for c in classes_spec:
        for day, spec in (c.get("schedule") or {}).items():
            for s in iter_day_classes(spec):
                if s:
                    used.add(str(s))
        # 班级私有科目覆盖里出现的名字同样要参与校验，
        # 否则给一个拼错的科目名配老师会被静默忽略。
        for s in (c.get("subjects") or {}):
            used.add(str(s))

    subs, sub_index = build_subjects(doc.get("subjects"), used)

    profile = S.empty_profile(S.sid("profile", doc.get("school", "school")),
                              name=doc.get("school", ""))
    profile["TimeLayouts"] = tls
    profile["Subjects"] = subs

    seen_ids: set[str] = set()
    seen_names: dict[str, str] = {}
    plans: dict = {}
    for c in classes_spec:
        # id 统一成字符串：YAML 里 202506 不加引号会被读成 int，
        # 与 split.py 的 str(c["id"]) 不一致就会错位。
        cid = c.get("id")
        cid = str(cid).strip() if cid is not None else ""
        cname = str(c.get("name") or cid).strip()
        if not cid:
            raise BuildError(f"班级 {cname!r} 缺少 id")
        if "/" in cid or cid.startswith("."):
            raise BuildError(f"非法班级 id {cid!r}（会当目录名用，不得含 / 或以 . 开头）")
        if cid in seen_ids:
            raise BuildError(f"班级 id 重复: {cid!r}")
        seen_ids.add(cid)

        # 班级名必须唯一且不含空格：课表名是 "<班名> <星期>"，
        # 重名或名字里带空格会让下游按名分组时串班（静默错输出）。
        if not cname:
            raise BuildError(f"班级 {cid!r} 的 name 不得为空")
        if " " in cname or "\u3000" in cname:
            raise BuildError(
                f"班级 {cid!r} 的 name {cname!r} 不得含空格"
                f"（课表名靠空格分隔班名与星期）")
        if cname in seen_names:
            raise BuildError(
                f"班级名重复: {cname!r} 同时用于 id "
                f"{seen_names[cname]!r} 和 {cid!r}")
        seen_names[cname] = cid

        # ── 本班独立科目表 ─────────────────────────────
        # 以全局科目表为底，叠加班级自己的 subjects: 覆盖（主要是老师名）。
        # GUID 不变：各班只加载自己那份文件，不会冲突；且保持 GUID 稳定
        # 才能让班级自己的 classplans.json 里的 SubjectId 继续命中。
        cls_sub_spec = c.get("subjects") or {}
        if not isinstance(cls_sub_spec, dict):
            raise BuildError(
                f"班级 {cid!r} 的 subjects 应为映射（科目名: {{teacher: ...}}），"
                f"实际是 {type(cls_sub_spec).__name__}")
        my_subs: dict = {}
        for sname, sgid in sub_index.items():
            rec = dict(subs[sgid])
            ov = cls_sub_spec.get(sname)
            if ov:
                if not isinstance(ov, dict):
                    raise BuildError(
                        f"班级 {cid!r} 科目 {sname!r} 的覆盖应为映射，"
                        f"如 {{teacher: 张三}}；实际是 {type(ov).__name__}")
                unknown_k = set(ov) - {"teacher", "initial", "outdoor"}
                if unknown_k:
                    # 拼错键静默忽略 = 老师名没生效且不报错，直接拦下
                    raise BuildError(
                        f"班级 {cid!r} 科目 {sname!r} 含未知字段 "
                        f"{sorted(unknown_k)}；可用: teacher / initial / outdoor")
                if ov.get("teacher"):
                    rec["TeacherName"] = str(ov["teacher"])
                if ov.get("initial"):
                    rec["Initial"] = str(ov["initial"])
                if "outdoor" in ov:
                    rec["IsOutDoor"] = bool(ov["outdoor"])
            my_subs[sgid] = rec
        class_subjects[cid] = my_subs

        sched = c.get("schedule") or {}
        reserved = bool(c.get("reserved", False))
        # 预留班级：先占住 id，课表以后再填。
        # 不允许「既无 schedule 也未声明 reserved」—— 那通常是误删，
        # 静默生成空课表会把大屏刷成空白。
        if reserved and sched:
            raise BuildError(
                f"班级 {cid!r} 既标了 reserved: true 又写了 schedule，"
                f"请二选一")
        if not reserved and not sched:
            raise BuildError(
                f"班级 {cid!r} 没有 schedule；"
                f"若为尚未排课的预留班级，请显式写 reserved: true")

        # 班级默认作息（每天可单独覆盖）。预留班级可不填。
        tl_name = c.get("timelayout")
        if reserved and tl_name is None:
            continue
        if tl_name not in tl_index:
            raise BuildError(
                f"班级 {cid!r} 引用了不存在的时间表 {tl_name!r}；"
                f"可用: {sorted(tl_index)}")
        if reserved:
            continue

        # 多周轮换总周数，默认双周
        weeks_total = int(c.get("weeks", 2))
        if weeks_total < 2:
            raise BuildError(f"班级 {cid!r} weeks 必须 >= 2（当前 {weeks_total}）")

        # 本班用到的作息 GUID（班级默认 + 各天覆盖）
        my_tl_gids: set[str] = {tl_index[tl_name][0]}

        for daykey, spec in (c.get("schedule") or {}).items():
            # ── 解析 "mon" / "mon@1"（@n = 多周轮换中的第 n 周） ──
            raw = str(daykey)
            div = 0
            if "@" in raw:
                raw, _, dtxt = raw.partition("@")
                if not dtxt.isdigit():
                    raise BuildError(
                        f"班级 {cid!r} 星期键 {daykey!r} 的 @ 后面必须是数字"
                        f"（@1=第1周 @2=第2周）")
                div = int(dtxt)
                if not 1 <= div <= weeks_total:
                    raise BuildError(
                        f"班级 {cid!r} {daykey!r}: 轮换周序号 {div} 超出范围，"
                        f"weeks={weeks_total} 时应为 1..{weeks_total}")
            if raw not in S.WEEKDAYS:
                raise BuildError(
                    f"班级 {cid!r} 未知的星期键 {raw!r}；"
                    f"应为 {'/'.join(S.WEEKDAYS)}（可加 @n 表示轮换周）")

            # ── 当天可覆盖作息：list 用班级默认，dict 可指定 timelayout ──
            day_tl = tl_name
            if isinstance(spec, dict):
                day_tl = spec.get("timelayout", tl_name)
                lst = spec.get("classes")
                if lst is None:
                    raise BuildError(
                        f"班级 {cid!r} {daykey!r}: dict 形式必须含 classes")
            else:
                lst = spec
            if day_tl not in tl_index:
                raise BuildError(
                    f"班级 {cid!r} {daykey!r} 引用了不存在的时间表 {day_tl!r}；"
                    f"可用: {sorted(tl_index)}")
            day_gid, n_slots = tl_index[day_tl]
            my_tl_gids.add(day_gid)

            lst = lst or []
            if len(lst) != n_slots:
                raise BuildError(
                    f"班级 {cid!r} {daykey}: 填了 {len(lst)} 节课，"
                    f"但时间表 {day_tl!r} 有 {n_slots} 个上课时间点。"
                    f"数量必须一致（无课处填 ~）")
            infos = []
            for s in lst:
                if not s:
                    infos.append(S.class_info(S.EMPTY_GUID))
                else:
                    gid = sub_index.get(str(s))
                    if gid is None:
                        raise BuildError(
                            f"班级 {cid!r} {daykey}: 未知科目 {s!r}")
                    infos.append(S.class_info(gid))

            wd = S.WEEKDAYS[raw]
            # 轮换课表名带周序号后缀，跟线上既有命名（周一1/周一2）一致
            plan_name = f"{cname} {S.WEEKDAY_CN[wd]}" + (str(div) if div else "")
            pid = S.sid("classplan", f"{cid}:{raw}:{div}")
            if pid in plans:
                raise BuildError(f"班级 {cid!r} 星期键 {daykey!r} 重复")
            plans[pid] = S.class_plan(
                plan_name, day_gid,
                S.time_rule(wd, div, weeks_total), infos)
            plan_owner[pid] = cid

        class_timelayouts[cid] = {g: tls[g] for g in sorted(my_tl_gids)}

    profile["ClassPlans"] = plans
    return profile


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default="schedule.yaml")
    ap.add_argument("-o", "--out", default="dist/Default.json")
    a = ap.parse_args()

    src = Path(a.src)
    if not src.exists():
        print(f"✗ 找不到 {src}", file=sys.stderr)
        return 1
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))

    try:
        warnings: list[str] = []
        profile = build(doc, warnings)
    except BuildError as e:
        print(f"✗ 构建失败: {e}", file=sys.stderr)
        return 1

    out = Path(a.out)
    S.dump_json(profile, out)
    print(f"✓ {out}")
    print(f"  时间表 {len(profile['TimeLayouts'])} | "
          f"科目 {len(profile['Subjects'])} | "
          f"课表 {len(profile['ClassPlans'])}")
    if warnings:
        print(f"  ⚠ {len(warnings)} 条告警（不阻止构建，但请确认是否符合预期）：")
        for w in warnings[:20]:
            print(f"    · {w}")
        if len(warnings) > 20:
            print(f"    …共 {len(warnings)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
