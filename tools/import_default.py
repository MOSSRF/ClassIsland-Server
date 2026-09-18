#!/usr/bin/env python3
"""
import_profile.py — 把客户端导出的单个 Default.json 导入 schedule.yaml，
并指定它归属的班级 id。

用法：
    # 导进现有 schedule.yaml（同 id 班级会被覆盖）
    python3 tools/import_profile.py Default.json --id 202506

    # 顺带指定显示名 / 输出到新文件（不动原课表）
    python3 tools/import_profile.py Default.json --id 202506 \
        --name 2025级6班 --schedule schedule.yaml -o schedule.new.yaml

设计：
    * 转换逻辑全在 src/import_profile.py（可被 Web 后端直接复用）；
    * 写回前一律过 build.py 校验，坏数据绝不落盘；
    * 默认覆盖同 id 班级（Web 端会先备份原文）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import vendor_bootstrap  # noqa: E402
vendor_bootstrap.activate()

import yaml  # noqa: E402

import import_profile as IP  # noqa: E402
from build import BuildError, build as build_profile  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="导入客户端 Default.json 到 schedule.yaml")
    ap.add_argument("profile", help="客户端导出的 Default.json 路径")
    ap.add_argument("--id", required=True, dest="cid", help="这份档案归属的班级 id")
    ap.add_argument("--name", default=None, help="班级显示名（默认从课表名反推）")
    ap.add_argument("--schedule", default="schedule.yaml", help="工作课表路径")
    ap.add_argument("-o", "--out", default=None, help="输出路径，默认覆盖 --schedule")
    args = ap.parse_args()

    sched_path = (ROOT / args.schedule) if not Path(args.schedule).is_absolute() \
        else Path(args.schedule)
    if not sched_path.exists():
        print(f"✗ 找不到工作课表 {sched_path}", file=sys.stderr)
        return 1
    text = sched_path.read_text(encoding="utf-8")
    doc = yaml.safe_load(text) or {}

    try:
        profile = IP.load_profile(args.profile)
        res = IP.merge_profile(doc, profile, args.cid, args.name)
    except IP.ImportError_ as e:
        print(f"✗ 导入失败：{e}", file=sys.stderr)
        return 1

    # 落盘前必须过正向构建：保证导入结果能被 split/publish 接受
    warnings: list[str] = []
    try:
        built = build_profile(res["doc"], warnings)
    except BuildError as e:
        print("✗ 导入的数据通不过构建校验，未写出任何文件：", file=sys.stderr)
        print(f"  {e}", file=sys.stderr)
        return 1

    out_path = (ROOT / args.out) if args.out else sched_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 由 tools/import_profile.py 从客户端 Default.json 合并\n"
        "# 线上既有 GUID 已用 guid: 钉住，勿删勿改。\n"
    )
    body = yaml.safe_dump(res["doc"], allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=100000)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(header + body, encoding="utf-8")
    tmp.replace(out_path)

    verb = "覆盖" if res["replaced"] else "新增"
    print(f"✓ 已{verb}班级 {args.cid}（{res['class']['name']}）→ {out_path}")
    print(f"  作息 {len(built['TimeLayouts'])} | 科目 {len(built['Subjects'])} "
          f"| 全校课表 {len(built['ClassPlans'])}")
    warns = res["warnings"] + warnings
    if warns:
        print(f"  ⚠ {len(warns)} 条提示：")
        for w in warns[:20]:
            print(f"    · {w}")
        if len(warns) > 20:
            print(f"    …共 {len(warns)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
