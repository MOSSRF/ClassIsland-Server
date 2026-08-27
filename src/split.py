#!/usr/bin/env python3
"""
split.py — schedule.yaml → dist/ 集控静态文件树

产物布局**与线上仓库既有约定完全一致**
（以实际使用中的集控仓库为准）：

    dist/manifest.json          集控入口（单份，服务全部班级）
    dist/timelayouts.json       共用·时间表
    dist/subjects.json          共用·科目
    dist/policy.json            共用·策略
    dist/settings.json          共用·默认设置（仅当 yaml 配了 settings）
    dist/{id}/classplans.json   每班一份课表
    dist/ManagementPreset.json  客户端装机预设（不上传仓库，单独发给装机的人）

为什么是单份 manifest：
  `ServerlessConnection::DecorateUrl` 的 IL 实测含两次 String::Replace，
  把 URL 模板里的 `{id}` 换成客户端自己的 ClassIdentity、`{cuid}` 换成客户端 ID。
  所以 ClassPlanSource.Value 里**直接写字面量 `{id}`** 即可按班分流，
  十几个班共用一份 manifest，不必每班生成一个清单文件。

🔴 Version 是客户端唯一的更新闸门
  `ReVersionString::IsNewerAndNotNull` 的 IL（25 字节，完整反汇编）：
      !string.IsNullOrWhiteSpace(Value) && this.Version > localVersion
                                                        ^^^ cgt 严格大于
  ⇒ 内容改了但 Version 没动，客户端**永远不会重新下载**（静默失效）。
  ⇒ 比较是 `>` 而非 `!=`，Version **不可回退**。
  本脚本用内容哈希自动管理 Version：内容真变了才 +1，没变则不动。

  ⚠️ 线上仓库现有四个 source 的 Version 全是 1 且从未变动过 —— 手工改 json
     后客户端拉不到新内容，正是这个原因。

Gitee raw 缓存实测：Cache-Control: max-age=60，走 varnish（X-Cache 由 MISS→HIT，
Age 递增）。TTL 只有 60 秒，所以**不需要**版本化文件名来绕缓存，
保持 URL 稳定 + 只涨 Version 更简单，也更贴合官方设计。

安全约定：只写 yaml 里声明的 id 目录，**绝不删除**仓库里已有的其他目录
（线上还有 202506/202508/202509/202510/HELLO/TEST 等在用或留档的 id）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_schema as S
from build import BuildError
from build import build as build_profile

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_POLICY = {
    "DisableProfileClassPlanEditing": True,
    "DisableProfileTimeLayoutEditing": True,
    "DisableProfileSubjectsEditing": True,
    "DisableProfileEditing": True,
    "DisableSettingsEditing": False,
    "DisableSplashCustomize": False,
    "DisableDebugMenu": True,
    # 留一条后路：集控出问题时现场老师能自救退出，不至于大屏变砖
    "AllowExitManagement": True,
    "DisableEasterEggs": False,
}


def sha16(txt: str) -> str:
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()[:16]


class Versions:
    """内容哈希 → Version 整数。只有内容真变了才 +1（且只增不减）。"""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.changed: list[str] = []

    def bump(self, key: str, content: str) -> int:
        h = sha16(content)
        prev = self.data.get(key)
        if prev and prev.get("sha") == h:
            return int(prev["v"])
        v = int(prev["v"]) + 1 if prev else 1
        self.data[key] = {"v": v, "sha": h}
        self.changed.append(f"{key} → v{v}")
        return v

    def save(self) -> None:
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default=str(ROOT / "schedule.yaml"))
    ap.add_argument("-o", "--out", default=str(ROOT / "dist"))
    ap.add_argument("--base-url", default=None,
                    help="覆盖 yaml 里的 publish.base_url")
    a = ap.parse_args()

    src = Path(a.src)
    if not src.exists():
        print(f"✗ 找不到 {src}", file=sys.stderr)
        return 1
    doc = yaml.safe_load(src.read_text(encoding="utf-8"))

    pub = doc.get("publish") or {}
    base = (a.base_url or pub.get("base_url") or "").rstrip("/")
    if not base:
        print("✗ 未配置 publish.base_url（也可用 --base-url 传入）", file=sys.stderr)
        return 1
    if "CHANGE_ME" in base:
        print("✗ publish.base_url 还是占位符，请填真实仓库地址", file=sys.stderr)
        return 1

    school = doc.get("school", "")
    out = Path(a.out)
    vers = Versions(ROOT / "versions.json")

    try:
        classes_spec = doc.get("classes") or []
        if not classes_spec:
            raise BuildError("classes 为空")

        # 直接复用 build.py 生成完整 Profile 再切片。
        # 校验（时间表/科目/严格科目名/节数对齐/单双周范围）全部由 build()
        # 一处完成 —— 这里绝不重复实现一遍，否则两条路径迟早行为不一致。
        # plan_owner 由 build() 回填 {课表GUID: 班级id}，比按班名前缀
        # 匹配可靠（重名/含空格会串班，而串班是静默错输出）。
        plan_owner: dict[str, str] = {}
        full = build_profile(doc, plan_owner=plan_owner)
    except BuildError as e:
        print(f"✗ 构建失败: {e}", file=sys.stderr)
        return 1

    pid = S.sid("profile", school or "school")

    def skel() -> dict:
        return S.empty_profile(pid, name=school)

    # ── 共用文件（URL 固定，只涨 Version） ──────────────────────
    def emit_shared(key: str, fname: str, payload: dict) -> int:
        txt = json.dumps(payload, ensure_ascii=False, indent=2)
        v = vers.bump(key, txt)
        p = out / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")
        return v

    tl = skel()
    tl["TimeLayouts"] = full["TimeLayouts"]
    v_tl = emit_shared("timelayout", "timelayouts.json", tl)

    sb = skel()
    sb["Subjects"] = full["Subjects"]
    v_sub = emit_shared("subjects", "subjects.json", sb)

    policy = dict(DEFAULT_POLICY)
    policy.update(doc.get("policy") or {})
    unknown = set(policy) - set(DEFAULT_POLICY)
    if unknown:
        print(f"✗ policy 含未知字段: {sorted(unknown)}", file=sys.stderr)
        return 1
    v_pol = emit_shared("policy", "policy.json", policy)

    settings = doc.get("settings") or {}
    v_set = emit_shared("settings", "settings.json", settings) if settings else 0

    # ── 每班 classplans.json ───────────────────────────────────
    # ClassPlanSource 在 manifest 里只有一个 Version，所以任一班级变动
    # 都要抬高这个共同的 ClassPlanVersion（客户端各自重拉自己那份，代价很小）。
    per_class: dict[str, str] = {}
    reserved_ids: list[str] = []
    for c in classes_spec:
        cid = str(c["id"]).strip()
        if "/" in cid or cid.startswith("."):
            print(f"✗ 非法班级 id: {cid!r}", file=sys.stderr)
            return 1
        mine = {k: v for k, v in full["ClassPlans"].items()
                if plan_owner.get(k) == cid}
        if not mine:
            # 预留班级：只占 id，尚未排课。不写文件也不报错，
            # 但也绝不写空课表 —— 空课表会把大屏刷成空白。
            if c.get("reserved"):
                reserved_ids.append(cid)
                continue
            print(f"✗ 班级 {cid!r} 没有生成任何课表", file=sys.stderr)
            return 1
        cp = skel()
        cp["ClassPlans"] = mine
        per_class[cid] = json.dumps(cp, ensure_ascii=False, indent=2)

    # 聚合哈希：任一班级内容变化 → ClassPlanVersion +1
    agg = "\n".join(f"{k}\n{per_class[k]}" for k in sorted(per_class))
    v_cp = vers.bump("classplan", agg)

    for cid, txt in per_class.items():
        p = out / cid / "classplans.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding="utf-8")

    # ── manifest.json（单份，{id} 由客户端替换） ────────────────
    manifest = {
        "ServerKind": 0,                       # 0 = Serverless（纯静态文件）
        "OrganizationName": school,
        "CoreVersion": S.CORE_VERSION,         # 2.0.0.0（IL 实测，≠ 程序集 2.1.0.1）
        "ClassPlanSource": S.re_version(f"{base}/{{id}}/classplans.json", v_cp),
        "TimeLayoutSource": S.re_version(f"{base}/timelayouts.json", v_tl),
        "SubjectsSource": S.re_version(f"{base}/subjects.json", v_sub),
        "PolicySource": S.re_version(f"{base}/policy.json", v_pol),
    }
    if settings:
        manifest["DefaultSettingsSource"] = S.re_version(
            f"{base}/settings.json", v_set)
    S.dump_json(manifest, out / "manifest.json")

    # ── 客户端装机预设（不进仓库，单独发给装机的人） ───────────
    # ClassIdentity 留空：每台大屏装好后填自己的班级 id，
    # 它就是 ClassPlanSource 里 {id} 的替换值。
    S.dump_json({
        "IsManagementEnabled": True,
        "ManagementServerKind": 0,
        "ManagementServer": "",
        "ManagementServerGrpc": "",
        "ManifestUrlTemplate": f"{base}/manifest.json",
        "ClassIdentity": "",
    }, out / "ManagementPreset.json")

    vers.save()

    print(f"✓ {out}")
    print(f"  班级 {len(per_class)} | 时间表 {len(full['TimeLayouts'])} | "
          f"科目 {len(full['Subjects'])}")
    if reserved_ids:
        print(f"  预留班级（已占 id、尚未排课，不生成文件）: "
              f"{', '.join(reserved_ids)}")
    print(f"  Version: classplan=v{v_cp} timelayout=v{v_tl} "
          f"subjects=v{v_sub} policy=v{v_pol}" +
          (f" settings=v{v_set}" if settings else ""))
    if vers.changed:
        print("  版本变更（需要 push）:")
        for c in vers.changed:
            print(f"    {c}")
    else:
        print("  内容无变化，Version 未动（不需要 push）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
