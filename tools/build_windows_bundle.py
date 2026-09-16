#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""build_windows_bundle.py — 把官方 Windows 客户端打成「开箱即加入集控」整合包。

官方 folder 版客户端在首次运行「加入管理」对话框时，会自动读取程序根目录
（folder 版即 <包目录>/data/）下的 ManagementPreset.json（见官方源码
ManagementService.ManagementPresetPath / JoinManagementDialog.OnInitialized）。
本脚本：

  1. 解包官方 ClassIsland_app_windows_x64_selfContained_folder.zip
     （自包含版，大屏机不需要另装 .NET 运行时）；
  2. 在 data/ 下放入 ManagementPreset.json（ManifestUrlTemplate 取自
     schedule.yaml 的 publish.base_url）；
  3. 额外在 data/class-presets/ 下为每个班级生成一份已填好 ClassIdentity
     的预设 —— 装机时把对应班级的文件复制/改名成 data/ManagementPreset.json
     即可，现场只需在「加入管理」对话框里点一次「连接」并确认；
  4. 放入中文装机说明。

注意（官方安全设计）：
  - 预设只负责预填地址/班级，客户端**仍会弹窗**让操作者确认「加入组织」，
    无法也不应静默入控。
  - 已加入过集控的客户端（data/Management/Settings.json 已存在）不会再
    自动弹预设；本整合包面向全新装机。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import vendor_bootstrap  # noqa: E402
vendor_bootstrap.activate()

import yaml  # noqa: E402

PRESET_NAME = "ManagementPreset.json"
README_NAME = "装机说明.txt"


def load_schedule(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = ((doc.get("publish") or {}).get("base_url") or "").rstrip("/")
    if not base or "CHANGE_ME" in base:
        raise SystemExit("✗ schedule.yaml 的 publish.base_url 未配置或仍是占位符")
    classes = []
    for c in doc.get("classes") or []:
        cid = str(c.get("id") or "").strip()
        if cid:
            classes.append((cid, str(c.get("name") or cid)))
    school = doc.get("school")
    if isinstance(school, dict):
        school = school.get("name")
    return {"base_url": base, "classes": classes,
            "school": school or "本校"}


def make_preset(base_url: str, class_id: str = "") -> dict:
    return {
        "IsManagementEnabled": True,
        "ManagementServerKind": 0,          # 0 = Serverless 静态清单
        "ManagementServer": "",
        "ManagementServerGrpc": "",
        "ManifestUrlTemplate": f"{base_url}/manifest.json",
        "ClassIdentity": class_id,
    }


def safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")


def build_readme(school: str, classes: list[tuple[str, str]]) -> str:
    lines = [
        "ClassIsland 集控整合包（Windows x64，自包含，无需另装 .NET）",
        "=" * 56,
        "",
        f"组织名称：{school}",
        "",
        "【全新机器装机步骤】",
        "1. 把整个文件夹解压到任意目录（不要只拖出 ClassIsland.exe）。",
        "2. 双击根目录的 ClassIsland.exe 启动，按首次引导完成基础设置。",
        "3. 打开「应用设置 → 集控选项 → 加入管理」：",
        "   - 对话框会自动加载本包 data 目录下的 ManagementPreset.json，",
        "     集控地址已预填好；",
        "   - 通用包默认未填班级 ID。请在「ID（可选）」一栏填写本班编号，",
        "     或先按下面【按班级装机】替换预设；",
        "   - 点「连接」，弹窗确认组织名称后点「加入」，完成。",
        "4. 加入成功后课表/作息/科目/策略会自动从集控服务器下发。",
        "",
        "【按班级装机（推荐，免填 ID）】",
        "本包 data/class-presets/ 内为每个班级准备了已填好 ID 的预设。",
        "装机前，把对应班级的预设复制到 data/ 并改名为",
        f"{PRESET_NAME}（覆盖通用预设）即可。班级清单：",
    ]
    for cid, cname in classes:
        lines.append(f"  - {cname}：ID = {cid}")
    lines += [
        "",
        "【注意】",
        "- 加入集控需要操作者在弹窗上手动确认，这是官方的安全设计，",
        "  无法静默加入。",
        "- 本包面向全新装机。已加入过其他集控的客户端请先在集控选项里",
        "  「退出管理」后再使用。",
        "- 卸载/排查：删除 data/Management 目录可清除集控状态。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 ClassIsland Windows 集控整合包")
    ap.add_argument("--zip", required=True,
                    help="官方 ClassIsland_app_windows_x64_selfContained_folder.zip")
    ap.add_argument("--schedule", default="schedule.yaml")
    ap.add_argument("--out", required=True, help="输出整合包 zip 路径")
    ap.add_argument("--class-id", default="",
                    help="只打指定班级（data 预设直接填好该 ID）；默认打通用包 + 全班预设")
    a = ap.parse_args()

    cfg = load_schedule(Path(a.schedule))
    src_zip = Path(a.zip)
    if not src_zip.exists():
        raise SystemExit(f"✗ 找不到官方客户端包：{src_zip}")

    classes = cfg["classes"]
    if a.class_id:
        hit = [c for c in classes if c[0] == a.class_id]
        if not hit:
            raise SystemExit(f"✗ schedule.yaml 里没有班级 id={a.class_id}")
        root_preset = make_preset(cfg["base_url"], a.class_id)
        class_presets = []
    else:
        root_preset = make_preset(cfg["base_url"], "")
        class_presets = classes

    readme = build_readme(cfg["school"], classes).encode("utf-8")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.zip")

    with zipfile.ZipFile(src_zip) as zin, \
            zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
        seen = set()
        for item in zin.infolist():
            seen.add(item.filename)
            zout.writestr(item, zin.read(item.filename))

        def put(path: str, data: bytes):
            if path in seen:
                raise SystemExit(f"✗ 包内已存在 {path}，拒绝覆盖官方文件")
            zout.writestr(path, data)

        # folder 版 AppRootFolderPath = <包根>/data（官方 App.axaml.cs 源码确认）
        put("data/" + PRESET_NAME,
            json.dumps(root_preset, ensure_ascii=False, indent=2).encode("utf-8"))
        put(README_NAME, readme)
        for cid, cname in class_presets:
            p = make_preset(cfg["base_url"], cid)
            put(f"data/class-presets/{safe_name(cname)}_{cid}_{PRESET_NAME}",
                json.dumps(p, ensure_ascii=False, indent=2).encode("utf-8"))

    tmp.replace(out)
    size_mb = out.stat().st_size / 1024 / 1024
    kind = f"班级 {a.class_id} 专用" if a.class_id else "通用（含按班预设）"
    print(f"✓ 已生成{kind}整合包：{out}  ({size_mb:.1f} MB)")
    print(f"  集控地址：{cfg['base_url']}/manifest.json")
    if not a.class_id:
        print(f"  班级预设：{len(class_presets)} 个 → data/class-presets/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
