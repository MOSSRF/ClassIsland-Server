#!/usr/bin/env python3
"""
yaml_edit.py — 对 schedule.yaml 做「按段替换」的外科手术式改写

为什么不用 yaml.safe_dump 整份重写：
    schedule.yaml 里有几类**必须原样保留**的东西，一旦被 dump 抹掉就是事故：

    1. `guid:` 钉桩旁边的警示注释
       （"线上既有 GUID 用 guid: 字段钉住，改动会导致客户端课表悬空，勿删"）
    2. 记录线上冗余重复项为何被删的注释
    3. timelayouts 段里 31 项作息的紧凑单行写法（dump 会摊成几百行，diff 全废）

    safe_dump 会丢掉全部注释、重排键顺序、改变行内风格。
    所以这里只替换「本次编辑真正碰过的段」，其余字节**逐字不动**。

1.1.0 起 Web 也能编辑 timelayouts / subjects / settings / school / publish。
    做法仍然是按段替换，并且**只替换用户这次真的改过的段**
    （webui 传 sections 列表过来）。没碰过的段一个字节都不动。

    ⚠️ 代价要说清楚：一旦某段被替换，该段内用户手写的自由注释会丢失
    （本模块只能重新生成结构，无法还原任意注释）。
    因此 dump_timelayouts / dump_subjects 会**主动重新输出 `guid:` 钉桩
    及其"勿删"警示注释** —— 这是不能丢的那部分，绝不能靠运气。
    guid 本身也逐字回写，改动它会让线上所有引用该作息的课表悬空。

保证：
    · 未被替换的段落 → 字节级不变
    · replace_section 找不到目标段会抛错，绝不静默追加或吞掉内容
    · upsert_section 用于「可能还不存在」的段（如 settings），显式追加
"""
from __future__ import annotations

import re

# 顶层键：行首非空白、形如 `key:`（YAML 顶层映射）
_TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):", re.M)

# 顶格（无缩进）的整行注释或空行。顶格是关键判据：
# 缩进的注释是段内某一项的旁注，顶格的成块注释才是「下一段的文档头」。
_HEADER_LINE = re.compile(r"^(?:#.*)?[ \t]*$")

GUID_WARN = (
    "# ⚠️ guid: 是线上既有 GUID 的钉桩，**勿删勿改**。\n"
    "#    线上班级课表通过它引用本作息；改动会让全部引用悬空，\n"
    "#    客户端拉到课表却找不到时间表（静默损坏，不报错）。"
)


def _indent(block: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + ln for ln in block.split("\n"))


def _trim_trailing_header(text: str, start: int, end: int) -> int:
    """把段尾那一坨「顶格注释 + 空行」让给下一段，返回收缩后的结束下标。

    ⚠️ 这是踩过的坑：原先 end 直接取「下一个顶层键的起始位置」，于是
    `timelayouts:` 段会一路吃掉它和 `classes:` 之间的所有内容 ——
    包括那几十行「# ── 科目 ──」「# ── 班级 ──」使用说明。
    结果：用户只在网页上把一个课间改长 5 分钟，保存后整段文档注释凭空消失。

    判据是**顶格**：`# 说明` 顶格写 = 给后面那段作文档头；
    `  # 周五提前放学` 有缩进 = 段内某一项的旁注，仍归本段
    （这类旁注在本段被重写时确实会丢，模块开头已说明）。
    """
    cut = end
    while cut > start:
        prev_nl = text.rfind("\n", start, cut - 1)
        line_start = prev_nl + 1 if prev_nl != -1 else start
        if line_start <= start:          # 别把本段第一行（`key:`）也让出去
            break
        line = text[line_start:cut].rstrip("\n")
        if line[:1] in (" ", "\t") or not _HEADER_LINE.fullmatch(line):
            break
        cut = line_start
    return cut


def find_sections(text: str) -> dict[str, tuple[int, int]]:
    """返回 {顶层键: (起始下标, 结束下标)}，区间含该段全部内容。

    结束下标从「下一个顶层键之前」再往回让出段尾的顶格注释块与空行
    —— 那些是下一段的文档头，不属于本段（见 _trim_trailing_header）。
    """
    hits = [(m.group(1), m.start()) for m in _TOP_KEY.finditer(text)]
    out: dict[str, tuple[int, int]] = {}
    for i, (key, start) in enumerate(hits):
        end = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        out[key] = (start, _trim_trailing_header(text, start, end))
    return out


def replace_section(text: str, key: str, new_block: str) -> str:
    """把 `key:` 整段替换为 new_block（new_block 需自带 `key:` 行）。

    找不到该段直接抛 KeyError —— 静默追加会造出重复顶层键，
    而 YAML 重复键是「后者覆盖前者」的静默错误。
    """
    secs = find_sections(text)
    if key not in secs:
        raise KeyError(f"schedule.yaml 里找不到顶层段 {key!r}；"
                       f"现有段: {sorted(secs)}")
    start, end = secs[key]
    block = new_block.rstrip("\n") + "\n"
    # 保留原段与下一段之间的空行数量，避免每次保存都产生空白抖动
    trailing = ""
    orig = text[start:end]
    m = re.search(r"(\n*)$", orig)
    if m:
        trailing = m.group(1)[1:] if m.group(1).startswith("\n") else m.group(1)
    return text[:start] + block + trailing + text[end:]


def upsert_section(text: str, key: str, new_block: str) -> str:
    """段存在就替换，不存在就追加到文件末尾。

    只给「可选段」用（settings / subjects / organization 这类一开始可能没写）。
    必需段一律走 replace_section —— 那里找不到就该报错，
    因为找不到必需段说明文件已经不对了，静默追加只会掩盖问题。
    """
    if key in find_sections(text):
        return replace_section(text, key, new_block)
    body = text if text.endswith("\n") else text + "\n"
    return body + "\n" + new_block.rstrip("\n") + "\n"


def entry_notes(text: str, section: str, indent: int = 2) -> dict[str, str]:
    """抓出段内每个条目上方的缩进注释，返回 {条目名: 注释块}。

    为何需要它：重写一段就会重新生成整段文本，用户写在条目上方的
    说明（如「# 周五提前放学，单独一张作息」）会悄无声息地消失。
    这类注释正是「为何这么配」的唯一记录，丢了没任何报错，
    只是下一个维护的人再也不知道原因 —— 所以必须原样抖回去。

    只抓「紧贴在条目上方、缩进不浅于条目」的连续注释行；
    遇到空行就停（空行隔开的注释归属不明，宁可不猜）。
    """
    secs = find_sections(text)
    if section not in secs:
        return {}
    start, end = secs[section]
    body = text[start:end]
    pad = " " * indent
    key_re = re.compile(
        r"^" + pad + r'(?![ \t#])("[^"]*"|[^:\n]+?):[ \t]*$', re.M)

    lines = body.split("\n")
    # 行起始下标 → 行号，用于从匹配位置倒推行号
    offs, acc = [], 0
    for ln in lines:
        offs.append(acc)
        acc += len(ln) + 1

    out: dict[str, str] = {}
    for m in key_re.finditer(body):
        name = m.group(1).strip().strip('"')
        li = 0
        for i, off in enumerate(offs):
            if off == m.start():
                li = i
                break
        buf = []
        j = li - 1
        while j >= 0:
            ln = lines[j]
            if ln.strip().startswith("#") and ln[:1] in (" ", "\t"):
                buf.append(ln)
                j -= 1
                continue
            break
        if buf:
            out[name] = "\n".join(reversed(buf))
    return out


def replace_scalar(text: str, key: str, value: str) -> str:
    """改写顶层标量，如 `school: 示例中学`。段不存在则追加。"""
    return upsert_section(text, key, f"{key}: {_q(value)}")


# ── 各段的文本生成器 ────────────────────────────────────────────

def _q(s: str) -> str:
    """需要时加引号：含特殊字符或可能被解析成非字符串时。"""
    s = str(s)
    if s == "":
        return '""'
    if re.search(r"[:#\[\]{},&*?|<>=!%@`\"']", s) or s.strip() != s:
        return '"' + s.replace('"', '\\"') + '"'
    # 纯数字/布尔样式的要引起来，否则被读成 int/bool
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", s) or s.lower() in (
            "true", "false", "null", "yes", "no", "on", "off", "~"):
        return f'"{s}"'
    return s


# policy 各字段的中文旁注。写在这里而不是只存于模板文件：
# 每次重写 policy 段都要把它们原样输出回去，否则用户在网页上拨一下开关，
# 整列「这个开关到底管什么」的说明就没了（yaml 是要给人看的）。
POLICY_NOTE = {
    "DisableProfileClassPlanEditing": "禁止本机改课表",
    "DisableProfileTimeLayoutEditing": "禁止本机改作息",
    "DisableProfileSubjectsEditing": "禁止本机改科目",
    "DisableProfileEditing": "禁止本机改档案",
    "DisableSettingsEditing": "禁止本机改设置",
    "DisableSplashCustomize": "禁止自定义启动画面",
    "DisableDebugMenu": "禁用调试菜单",
    "AllowExitManagement": "允许退出集控（建议保持 true，便于排障）",
    "DisableEasterEggs": "禁用彩蛋（非官方文档字段，实测得来）",
}


def _url(s: str) -> str:
    """URL 不加引号输出（如果安全）。

    _q 会因为 URL 里的 `:` 而把它包成 "https://..."，虽然 YAML 上等价，
    但会让每次保存都在 diff 里凭空多一行变更。YAML 里 `k: https://a`
    是合法的 —— `:` 只在后跟空白时才是键值分隔符。
    """
    s = str(s or "")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s#\"']+", s):
        return s
    return _q(s)


def dump_policy(policy: dict, order: list[str]) -> str:
    """policy 段：固定 9 个字段，按官方顺序输出，并带上中文旁注。"""
    L = ["policy:"]
    width = max(len(k) for k in order) + 2 if order else 0
    for k in order:
        v = "true" if bool(policy.get(k, False)) else "false"
        note = POLICY_NOTE.get(k)
        line = f"  {k}: {v}"
        if note:
            line = f"  {(k + ':').ljust(width)}{v.ljust(6)}  # {note}"
        L.append(line.rstrip())
    return "\n".join(L)


def _time(s) -> str:
    """时间一律带引号输出：`07:30` 不加引号会被 YAML 读成 sexagesimal int。"""
    return '"' + str(s).strip() + '"'


def dump_timelayouts(layouts: dict, notes: dict | None = None) -> str:
    """timelayouts 段：还原紧凑单行写法，并重新输出 guid 钉桩与警示注释。

    入参形如：
        {"秋季时间表": {"guid": "xxx" | None,
                        "items": [{"start","end","type","name"}, ...]}}

    输出：
        timelayouts:
          秋季时间表:
            # ⚠️ guid: ...（警示注释，仅在有钉桩时输出）
            guid: 3f2a...
            items:
              - { start: "07:20", end: "07:50", type: class }
              - { start: "11:40", type: divider }

    分割线/行动是零长度标记，官方写法就是不带 end —— 这里也不输出 end，
    否则 diff 里凭空多出一堆 end 字段，且容易让人误以为可以调它的时长。
    """
    L = ["timelayouts:"]
    notes = notes or {}
    for name, node in layouts.items():
        items = node.get("items") if isinstance(node, dict) else node
        guid = node.get("guid") if isinstance(node, dict) else None
        if notes.get(name):                 # 原文里写在该作息上方的说明，原样抖回
            L.append(notes[name])
        L.append(f"  {_q(name)}:")
        if guid:
            L.append(_indent(GUID_WARN, 4))
            L.append(f"    guid: {guid}")
        L.append("    items:")
        for it in items or []:
            tt = str(it.get("type", "class") or "class")
            parts = [f"start: {_time(it.get('start', ''))}"]
            if tt in ("class", "break"):
                parts.append(f"end: {_time(it.get('end', ''))}")
            parts.append(f"type: {tt}")
            if tt == "break" and (it.get("name") or "").strip():
                parts.append(f"name: {_q(it['name'].strip())}")
            L.append("      - { " + ", ".join(parts) + " }")
        L.append("")
    return "\n".join(L).rstrip("\n")


def dump_subjects(subjects: dict) -> str:
    """subjects 段：只输出**自定义科目**（官方 21 科不必声明）。

    每个科目压成单行 flow 映射，字段全空时也要留 `{}`
    —— 写成 `校本课程:` 后面空着会被 YAML 读成 None，
    build.py 里 `meta or {}` 能兜住，但显式 `{}` 更不容易看错。
    """
    L = ["subjects:"]
    if not subjects:
        # 空段：留注释说明，避免下次读到一个空 map 以为丢了数据
        L.append("  # （没有自定义科目：官方 21 科直接写名字即可，无需声明）")
        return "\n".join(L)
    has_guid = any((m or {}).get("guid") for m in subjects.values())
    if has_guid:
        L.append(_indent(
            GUID_WARN.replace("本作息", "本科目").replace("时间表", "科目"), 2))
    for name in subjects:
        meta = subjects[name] or {}
        parts = []
        if meta.get("guid"):
            parts.append(f"guid: {meta['guid']}")
        if (meta.get("initial") or "").strip():
            parts.append(f"initial: {_q(meta['initial'].strip())}")
        if (meta.get("teacher") or "").strip():
            parts.append(f"teacher: {_q(meta['teacher'].strip())}")
        if meta.get("outdoor"):
            parts.append("outdoor: true")
        L.append(f"  {_q(name)}: {{ {', '.join(parts)} }}" if parts
                 else f"  {_q(name)}: {{}}")
    return "\n".join(L)


def dump_settings(settings: dict) -> str:
    """settings 段：客户端默认设置（manifest 的 DefaultSettingsSource）。

    键名由 ClassIsland 的 Settings 模型决定，本项目**不做键名白名单**
    —— 猜错反而会拦住合法配置。这里按 JSON 兼容子集输出，保持可读。
    """
    L = ["settings:"]
    if not settings:
        L.append("  # （留空 = 不下发默认设置，manifest 不会带"
                 " DefaultSettingsSource）")
        return "\n".join(L)
    L.append("  # 键名必须与 ClassIsland 的设置模型一致，本工具不校验键名。")
    for k, v in settings.items():
        L.append(f"  {k}: {_scalar(v)}")
    return "\n".join(L)


def _scalar(v) -> str:
    """把 Python 值写成 YAML 标量/flow 形式（settings 用，深度有限）。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if v is None:
        return "null"
    if isinstance(v, list):
        return "[" + ", ".join(_scalar(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k}: {_scalar(x)}" for k, x in v.items()) + " }"
    return _q(v)


def dump_publish(base_url: str) -> str:
    L = ["publish:",
         "  # 静态文件的公开访问根地址。",
         "  # 用 Gitee/GitHub 仓库的 raw 地址即可，注意分支名要对。",
         "  #   Gitee : https://gitee.com/<用户>/<仓库>/raw/master",
         "  #   GitHub: https://raw.githubusercontent.com/<用户>/<仓库>/main",
         f"  base_url: {_url(base_url)}"]
    return "\n".join(L)


def dump_classes(classes: list[dict]) -> str:
    """classes 段：还原 import_live.py 的紧凑风格，保证 diff 可读。

    每个班级：
        - id: "202506"
          name: 2025级6班
          timelayout: 秋季时间表
          weeks: 2                 # 仅当 != 2 时输出
          schedule:
            mon@1: [早读, 英语, ...]
            fri@1: { timelayout: 秋季时间表（周五）, classes: [...] }

    预留班级压成单行：
        - { id: "202501", name: 2025级1班, reserved: true }
    """
    L = ["classes:"]
    normal = [c for c in classes if not c.get("reserved")]
    reserved = [c for c in classes if c.get("reserved")]

    for c in normal:
        L.append(f'  - id: {_q(c["id"])}')
        L.append(f'    name: {_q(c.get("name") or c["id"])}')
        if c.get("timelayout"):
            L.append(f'    timelayout: {_q(c["timelayout"])}')
        weeks = int(c.get("weeks", 2))
        if weeks != 2:
            L.append(f"    weeks: {weeks}")
        # 本班任课老师等覆盖。必须原样写回：漏掉它，从网页保存一次
        # 就会把全班老师名静默抹掉（内容看着还在，老师全没了）。
        my_subs = c.get("subjects") or {}
        if my_subs:
            L.append("    subjects:")
            for nm in sorted(my_subs):
                meta = my_subs[nm] or {}
                bits = []
                if meta.get("teacher"):
                    bits.append(f'teacher: {_q(str(meta["teacher"]))}')
                if meta.get("initial"):
                    bits.append(f'initial: {_q(str(meta["initial"]))}')
                if "outdoor" in meta:
                    bits.append(
                        f'outdoor: {"true" if meta["outdoor"] else "false"}')
                if bits:
                    L.append(f"      {_q(nm)}: {{ {', '.join(bits)} }}")
        sched = c.get("schedule") or {}
        if sched:
            L.append("    schedule:")
            for day, spec in sched.items():
                if isinstance(spec, dict):
                    items = ", ".join(_q(x) if x else "~"
                                      for x in (spec.get("classes") or []))
                    tl = _q(spec.get("timelayout", ""))
                    L.append(f"      {day}: {{ timelayout: {tl}, "
                             f"classes: [{items}] }}")
                else:
                    items = ", ".join(_q(x) if x else "~" for x in (spec or []))
                    L.append(f"      {day}: [{items}]")
        L.append("")

    if reserved:
        L.append("  # ── 预留班级 ──────────────────────────────────────────────")
        L.append("  # reserved: true = 只占住 id，尚未排课。")
        L.append("  #   · 不会生成 <id>/classplans.json（空课表会把大屏刷成空白）")
        L.append("  #   · 排课时：删掉 reserved，补上 timelayout + schedule 即可")
        L.append("  #   · 删班级：删掉整段后跑 tools/prune_ids.py 清理线上残留目录")
        for c in reserved:
            L.append(f'  - {{ id: {_q(c["id"])}, '
                     f'name: {_q(c.get("name") or c["id"])}, reserved: true }}')
    return "\n".join(L)
