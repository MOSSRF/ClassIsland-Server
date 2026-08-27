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
    所以这里只替换「Web 界面真正会改的段」（policy / classes），
    其余字节**逐字不动**。

保证：
    · 未被替换的段落 → 字节级不变
    · replace_section 找不到目标段会抛错，绝不静默追加或吞掉内容
"""
from __future__ import annotations

import re

# 顶层键：行首非空白、形如 `key:`（YAML 顶层映射）
_TOP_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):", re.M)


def find_sections(text: str) -> dict[str, tuple[int, int]]:
    """返回 {顶层键: (起始下标, 结束下标)}，区间含该段全部内容。

    结束下标取「下一个顶层键之前」，因此段尾的注释与空行都归属本段
    —— 这正是我们要保留的东西。
    """
    hits = [(m.group(1), m.start()) for m in _TOP_KEY.finditer(text)]
    out: dict[str, tuple[int, int]] = {}
    for i, (key, start) in enumerate(hits):
        end = hits[i + 1][1] if i + 1 < len(hits) else len(text)
        out[key] = (start, end)
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


def dump_policy(policy: dict, order: list[str]) -> str:
    """policy 段：固定 9 个字段，按官方顺序输出。"""
    L = ["policy:"]
    for k in order:
        v = bool(policy.get(k, False))
        L.append(f"  {k}: {'true' if v else 'false'}")
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
