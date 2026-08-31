#!/usr/bin/env python3
"""
webui.py — 课表可视化编辑界面（单文件 HTTP 服务，零前端构建链）

设计取舍：
    · 不引 npm/框架：NAS 上不折腾前端构建链，原生 HTML/JS 足够
    · 只用标准库 http.server：商店里没有 flask/fastapi，也没必要装
    · **所有校验仍归 build.py**：这里绝不重复实现一遍校验逻辑，
      否则两条路径迟早行为不一致（这个原则前面已经踩过教训）
    · 保存 = yaml_edit 按段替换：注释与 timelayouts 紧凑写法逐字保留
    · 发布必须过 preflight：预检非 0 直接挡住，不给推

启动：
    python3 tools/webui.py --port 8848
    然后浏览器开 http://<NAS_IP>:8848

安全：
    默认只绑 0.0.0.0 供局域网用；这是内网工具，不做鉴权。
    不要暴露到公网 —— 它能改课表并 push。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml                                    # noqa: E402
import appconfig                                # noqa: E402
import ci_schema as S                          # noqa: E402
import yaml_edit as YE                         # noqa: E402
from build import BuildError, build as build_profile   # noqa: E402

# 环境相关配置全部来自 appconfig（config.json / CISRV_* 环境变量）
LIVE_REPO = appconfig.live_repo()
BACKUP_DIR = appconfig.backup_dir()      # None = 用户显式关闭了备份


def backup_write(name: str, data: bytes) -> str | None:
    """写一份备份，返回文件名；备份关闭时返回 None。

    备份是本地保险，不是流水线的必要环节（真正的历史在目标仓库的 git 里），
    所以必须允许关闭。集中在这一个函数里，避免每个调用点都写一遍 if。
    """
    if BACKUP_DIR is None:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    (BACKUP_DIR / name).write_bytes(data)
    return name

POLICY_ORDER = [
    "DisableProfileClassPlanEditing", "DisableProfileTimeLayoutEditing",
    "DisableProfileSubjectsEditing", "DisableProfileEditing",
    "DisableSettingsEditing", "DisableSplashCustomize",
    "DisableDebugMenu", "AllowExitManagement", "DisableEasterEggs",
]
POLICY_LABEL = {
    "DisableProfileClassPlanEditing": "禁止本机改课表",
    "DisableProfileTimeLayoutEditing": "禁止本机改作息",
    "DisableProfileSubjectsEditing": "禁止本机改科目",
    "DisableProfileEditing": "禁止本机改档案",
    "DisableSettingsEditing": "禁止本机改设置",
    "DisableSplashCustomize": "禁止自定义启动画面",
    "DisableDebugMenu": "禁用调试菜单",
    "AllowExitManagement": "允许退出集控",
    "DisableEasterEggs": "禁用彩蛋",
}
DAY_LABEL = {"mon": "周一", "tue": "周二", "wed": "周三", "thu": "周四",
             "fri": "周五", "sat": "周六", "sun": "周日"}
DAY_ORDER = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

_lock = threading.Lock()


# ── 数据读取 ────────────────────────────────────────────────────

def yaml_path(name: str) -> Path:
    p = ROOT / name
    # 防目录穿越：只允许工作区根下的 .yaml
    if p.parent != ROOT or p.suffix not in (".yaml", ".yml"):
        raise ValueError(f"非法文件名: {name!r}")
    return p


def _hhmm(v) -> str:
    """把 yaml 读出来的时间统一成 'HH:MM' 字符串。

    ⚠️ YAML 1.1 会把不加引号的 `07:30` 解析成 sexagesimal 整数 450，
    把 `7:20:00` 解析成 26400。这里必须还原，否则前端时间框会显示成数字，
    用户一保存就把作息写坏（静默错数据）。
    """
    if isinstance(v, int):
        # 60 进制还原：450 -> 7*60+30；带秒的按 3 段还原
        h, m = divmod(v, 60)
        if h >= 24:                      # 说明原文是 H:M:S
            total = v
            h, rem = divmod(total, 3600)
            m, s = divmod(rem, 60)
            return f"{h:02d}:{m:02d}" if s == 0 else f"{h:02d}:{m:02d}:{s:02d}"
        return f"{h:02d}:{m:02d}"
    return str(v or "").strip()


def layout_slots(items: list) -> dict:
    """从作息项算出「上课槽 + 标记」，供课表网格渲染用。"""
    slots, marks = [], []
    for it in items or []:
        tt = str(it.get("type", "class") or "class")
        st, et = _hhmm(it.get("start")), _hhmm(it.get("end"))
        if tt == "class":
            slots.append({"start": st, "end": et})
        elif tt == "divider":
            # 分割线：记录它出现在第几个上课槽之后，前端画分隔
            marks.append({"after": len(slots), "kind": "divider"})
        elif tt == "break" and it.get("name"):
            marks.append({"after": len(slots), "kind": "break",
                          "name": it.get("name")})
    return {"slots": slots, "marks": marks}


def read_state(fname: str) -> dict:
    """把 yaml 转成前端要的结构。作息里带上每个上课槽的真实时间。"""
    text = yaml_path(fname).read_text(encoding="utf-8")
    doc = yaml.safe_load(text) or {}

    # 每个作息：既给网格渲染用的 slots/marks，也给作息编辑器用的原始 items
    layouts = {}
    raw_layouts = {}
    for name, node in (doc.get("timelayouts") or {}).items():
        items = node.get("items") if isinstance(node, dict) else node
        guid = node.get("guid") if isinstance(node, dict) else None
        layouts[name] = layout_slots(items)
        raw_layouts[name] = {
            "guid": str(guid) if guid else None,
            "items": [{
                "start": _hhmm(it.get("start")),
                "end": _hhmm(it.get("end")),
                "type": str(it.get("type", "class") or "class"),
                "name": str(it.get("name") or ""),
            } for it in (items or [])],
        }

    official = S.load_official_subjects()
    custom_raw = doc.get("subjects") or {}
    # 自定义科目：完整元数据给编辑器；officialSubjects 让前端能标出「官方科目
    # 不可编辑」—— 官方 21 科的 Initial/IsOutDoor 由上游定义，抄一份到 yaml
    # 就等于把它冻结在今天的值上，以后上游改了我们还在下发旧值。
    custom = {}
    for name, meta in custom_raw.items():
        meta = meta or {}
        custom[str(name)] = {
            "guid": str(meta.get("guid")) if meta.get("guid") else None,
            "initial": str(meta.get("initial") or ""),
            "teacher": str(meta.get("teacher") or ""),
            "outdoor": bool(meta.get("outdoor", False)),
        }
    subjects = sorted(set(official) | set(custom))

    classes = []
    for c in doc.get("classes") or []:
        classes.append({
            "id": str(c.get("id", "")),
            "name": str(c.get("name") or c.get("id", "")),
            "timelayout": c.get("timelayout") or "",
            "weeks": int(c.get("weeks", 2)),
            "reserved": bool(c.get("reserved", False)),
            "schedule": c.get("schedule") or {},
        })

    policy = {k: bool((doc.get("policy") or {}).get(k, False))
              for k in POLICY_ORDER}

    return {
        "file": fname,
        "school": doc.get("school", ""),
        "organization": str(doc.get("organization") or ""),
        "base_url": (doc.get("publish") or {}).get("base_url", ""),
        "policy": policy,
        "policyOrder": POLICY_ORDER,
        "policyLabel": POLICY_LABEL,
        "timelayouts": layouts,
        "rawLayouts": raw_layouts,
        "subjects": subjects,
        "officialSubjects": sorted(official),
        "customSubjects": custom,
        "settings": doc.get("settings") or {},
        "classes": classes,
        "dayOrder": DAY_ORDER,
        "dayLabel": DAY_LABEL,
        "backupEnabled": BACKUP_DIR is not None,
    }


# ── 保存（按段替换，其余字节不动） ────────────────────────────────

# 前端会告诉后端「这次改了哪些段」。只重写改过的段是刻意设计：
# 每次重写一段都会丢掉该段内用户手写的自由注释（本工具只能重新生成结构），
# 所以碰得越少越好 —— 没改过的段一个字节都不动。
EDITABLE_SECTIONS = ("policy", "classes", "timelayouts", "subjects",
                     "settings", "school", "organization", "publish")


def _apply_sections(text: str, body: dict, sections: list[str]) -> str:
    """按 sections 逐段改写，返回新文本。"""
    new = text
    for sec in sections:
        if sec not in EDITABLE_SECTIONS:
            raise ValueError(f"未知的可编辑段: {sec!r}；"
                             f"可用: {list(EDITABLE_SECTIONS)}")
        if sec == "policy":
            new = YE.replace_section(
                new, "policy", YE.dump_policy(body.get("policy") or {},
                                              POLICY_ORDER))
        elif sec == "classes":
            new = YE.replace_section(
                new, "classes", YE.dump_classes(body.get("classes") or []))
        elif sec == "timelayouts":
            # 把原文里写在每张作息上方的说明抓出来再抖回去。
            # 不做的后果：用户只把一个课间改长 5 分钟，「# 周五提前放学」
            # 这类「为何这么配」的唯一记录就静静消失了。
            new = YE.replace_section(
                new, "timelayouts",
                YE.dump_timelayouts(body.get("timelayouts") or {},
                                    YE.entry_notes(new, "timelayouts")))
        elif sec == "subjects":
            # 官方 21 科不该被写进 subjects: —— 那样等于把官方定义抄一份，
            # 以后官方改了 Initial/IsOutDoor 我们还在下发旧值。
            new = YE.upsert_section(
                new, "subjects",
                YE.dump_subjects(body.get("subjects") or {}))
        elif sec == "settings":
            new = YE.upsert_section(
                new, "settings", YE.dump_settings(body.get("settings") or {}))
        elif sec == "school":
            new = YE.replace_scalar(new, "school", body.get("school") or "")
        elif sec == "organization":
            new = YE.replace_scalar(new, "organization",
                                    body.get("organization") or "")
        elif sec == "publish":
            new = YE.replace_section(
                new, "publish", YE.dump_publish(body.get("base_url") or ""))
    return new


def save_state(fname: str, body: dict) -> dict:
    p = yaml_path(fname)
    text = p.read_text(encoding="utf-8")

    # 未指定 sections 时退回旧行为（policy + classes），保持老前端可用
    sections = body.get("sections") or ["policy", "classes"]
    new = _apply_sections(text, body, list(sections))

    # 保存前先构建一次：语法/引用错误就地拦下，绝不写出坏 yaml。
    # 校验只有 build.py 一处实现 —— 这里只是调用它。
    doc = yaml.safe_load(new)
    warnings: list[str] = []
    build_profile(doc, warnings)          # 抛 BuildError 就让上层返回 400

    # 原子写 + 备份原文件（备份可在 config.json 里关闭）
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = backup_write(f"{ts}.{p.stem}.yaml", text.encode("utf-8"))
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, p)
    return {"ok": True, "warnings": warnings, "backup": bak,
            "sections": list(sections)}


# ── 构建 / 预检 / 发布 ──────────────────────────────────────────

def run(cmd: list[str], cwd: Path, env: dict | None = None) -> dict:
    e = appconfig.git_env()
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True,
                       text=True, timeout=300)
    return {"cmd": " ".join(cmd), "rc": r.returncode,
            "out": (r.stdout or "") + (r.stderr or "")}


def do_build(fname: str) -> dict:
    return run([sys.executable, "src/build.py", fname,
                "-o", "dist/Default.json"], ROOT)


def do_preflight(fname: str) -> dict:
    steps = [run([sys.executable, "src/split.py", fname, "-o", "dist_live"], ROOT)]
    if steps[-1]["rc"] == 0:
        steps.append(run([sys.executable, "tools/preflight.py",
                          "--dist", "dist_live", "--live", str(LIVE_REPO)], ROOT))
    return {"steps": steps, "rc": max(s["rc"] for s in steps)}


def do_publish(fname: str, message: str) -> dict:
    """split → preflight → 拷进仓库 → commit → push。预检不过就中止。"""
    steps = [run([sys.executable, "src/split.py", fname, "-o", "dist_live"], ROOT)]
    if steps[-1]["rc"] != 0:
        return {"steps": steps, "rc": steps[-1]["rc"], "stage": "split"}

    pf = run([sys.executable, "tools/preflight.py",
              "--dist", "dist_live", "--live", str(LIVE_REPO)], ROOT)
    steps.append(pf)
    if pf["rc"] != 0:
        # 关键闸门：预检不过绝不发布
        return {"steps": steps, "rc": pf["rc"], "stage": "preflight"}

    dist = ROOT / "dist_live"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    copied = []
    for f in sorted(dist.rglob("*")):
        if not f.is_file() or f.name == "ManagementPreset.json":
            continue
        rel = f.relative_to(dist)
        dst = LIVE_REPO / rel
        # 覆盖前按 [时间.id] 备份线上原文件（备份可在 config.json 里关闭）
        if dst.exists():
            tag = rel.parent.name if rel.parent != Path(".") else "_shared"
            backup_write(f"{ts}.{tag}.{rel.name}", dst.read_bytes())
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(f.read_bytes())
        copied.append(str(rel))
    note = (f"备份至 {BACKUP_DIR}（前缀 {ts}）" if BACKUP_DIR
            else "备份已关闭（config.json 的 backup_dir 为空）")
    steps.append({"cmd": "backup+copy", "rc": 0,
                  "out": note + "\n更新 " + ", ".join(copied)})

    st = run(["git", "status", "--porcelain"], LIVE_REPO)
    steps.append(st)
    if not st["out"].strip():
        steps.append({"cmd": "(skip)", "rc": 0, "out": "线上内容无变化，无需提交"})
        return {"steps": steps, "rc": 0, "stage": "nochange"}

    steps.append(run(["git", "add", "-A"], LIVE_REPO))
    msg = (message or "").strip() or "由流水线生成：课表更新"
    steps.append(run(["git", "-c", "user.name=moss-pipeline",
                      "-c", "user.email=moss@nas.local",
                      "commit", "-q", "-m", msg], LIVE_REPO))
    push = run(["git", "push", "origin", appconfig.git_branch()], LIVE_REPO)
    steps.append(push)
    return {"steps": steps, "rc": push["rc"], "stage": "push"}


# ── HTTP ───────────────────────────────────────────────────────

class H(BaseHTTPRequestHandler):
    server_version = "ClassIslandWebUI"

    def log_message(self, fmt, *a):     # 静音访问日志
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(),
                   "application/json; charset=utf-8")

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html"):
                html = (Path(__file__).parent / "webui.html").read_bytes()
                return self._send(200, html, "text/html; charset=utf-8")
            if path == "/api/state":
                return self._json(read_state(FILE))
            if path == "/api/preset":
                # ManagementPreset.json：放到客户端程序目录即可自动加载集控。
                # 之前它只生成在 dist/ 里，用户在界面上拿不到，只能自己去
                # 翻目录 —— 官方文档明确它是装机分发用的，必须能下载。
                return self._preset()
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _preset(self):
        text = yaml_path(FILE).read_text(encoding="utf-8")
        doc = yaml.safe_load(text) or {}
        base = ((doc.get("publish") or {}).get("base_url") or "").rstrip("/")
        if not base or "CHANGE_ME" in base:
            return self._json(
                {"error": "publish.base_url 未配置或仍是占位符，"
                          "无法生成装机预设"}, 400)
        preset = {
            "IsManagementEnabled": True,
            "ManagementServerKind": 0,
            "ManagementServer": "",
            "ManagementServerGrpc": "",
            "ManifestUrlTemplate": f"{base}/manifest.json",
            # 留空：每台大屏装好后填自己的班级 id，它就是 URL 里 {id} 的值
            "ClassIdentity": "",
        }
        body = json.dumps(preset, ensure_ascii=False, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="ManagementPreset.json"')
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._json({"error": f"请求体解析失败: {e}"}, 400)

        try:
            with _lock:                       # 串行化，避免并发写坏 yaml
                if path == "/api/save":
                    return self._json(save_state(FILE, body))
                if path == "/api/build":
                    return self._json(do_build(FILE))
                if path == "/api/preflight":
                    return self._json(do_preflight(FILE))
                if path == "/api/publish":
                    return self._json(do_publish(FILE, body.get("message", "")))
            return self._json({"error": "not found"}, 404)
        except BuildError as e:
            # 校验失败是预期路径：明确告诉用户哪里错，不写文件
            return self._json({"error": f"校验未通过：{e}"}, 400)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


FILE = "schedule.yaml"


def main() -> int:
    global FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--file", default="schedule.yaml")
    a = ap.parse_args()
    FILE = a.file

    if not yaml_path(FILE).exists():
        print(f"✗ 找不到 {ROOT / FILE}", file=sys.stderr)
        return 1

    srv = ThreadingHTTPServer((a.host, a.port), H)
    print(f"✓ 课表编辑界面: http://{a.host}:{a.port}   (编辑 {FILE})")
    print("  Ctrl-C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
