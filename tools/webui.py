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
BACKUP_DIR = appconfig.backup_dir()

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


def read_state(fname: str) -> dict:
    """把 yaml 转成前端要的结构。作息里带上每个上课槽的真实时间。"""
    text = yaml_path(fname).read_text(encoding="utf-8")
    doc = yaml.safe_load(text) or {}

    # 每个作息的上课槽时间（前端用来标注行）
    layouts = {}
    for name, node in (doc.get("timelayouts") or {}).items():
        items = node.get("items") if isinstance(node, dict) else node
        slots, marks = [], []
        for it in items or []:
            tt = str(it.get("type", "class"))
            st, et = it.get("start", ""), it.get("end", "")
            if tt == "class":
                slots.append({"start": st, "end": et})
            elif tt == "divider":
                # 分割线：记录它出现在第几个上课槽之后，前端画分隔
                marks.append({"after": len(slots), "kind": "divider"})
            elif tt == "break" and it.get("name"):
                marks.append({"after": len(slots), "kind": "break",
                              "name": it.get("name")})
        layouts[name] = {"slots": slots, "marks": marks}

    official = S.load_official_subjects()
    extra = list((doc.get("subjects") or {}).keys())
    subjects = sorted(set(official) | set(extra))

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
        "base_url": (doc.get("publish") or {}).get("base_url", ""),
        "policy": policy,
        "policyOrder": POLICY_ORDER,
        "policyLabel": POLICY_LABEL,
        "timelayouts": layouts,
        "subjects": subjects,
        "classes": classes,
        "dayOrder": DAY_ORDER,
        "dayLabel": DAY_LABEL,
    }


# ── 保存（按段替换，其余字节不动） ────────────────────────────────

def save_state(fname: str, policy: dict, classes: list) -> dict:
    p = yaml_path(fname)
    text = p.read_text(encoding="utf-8")

    new = YE.replace_section(text, "policy", YE.dump_policy(policy, POLICY_ORDER))
    new = YE.replace_section(new, "classes", YE.dump_classes(classes))

    # 保存前先构建一次：语法/引用错误就地拦下，绝不写出坏 yaml
    doc = yaml.safe_load(new)
    warnings: list[str] = []
    build_profile(doc, warnings)          # 抛 BuildError 就让上层返回 400

    # 原子写 + 时间戳备份，改坏了能捞回来
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    (BACKUP_DIR / f"{ts}.{p.stem}.yaml").write_text(text, encoding="utf-8")
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, p)
    return {"ok": True, "warnings": warnings,
            "backup": f"{ts}.{p.stem}.yaml"}


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
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    copied = []
    for f in sorted(dist.rglob("*")):
        if not f.is_file() or f.name == "ManagementPreset.json":
            continue
        rel = f.relative_to(dist)
        dst = LIVE_REPO / rel
        # 覆盖前按 [时间.id] 备份线上原文件
        if dst.exists():
            tag = rel.parent.name if rel.parent != Path(".") else "_shared"
            (BACKUP_DIR / f"{ts}.{tag}.{rel.name}").write_bytes(dst.read_bytes())
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(f.read_bytes())
        copied.append(str(rel))
    steps.append({"cmd": "backup+copy", "rc": 0,
                  "out": f"备份至 {BACKUP_DIR}（前缀 {ts}）\n更新 "
                         + ", ".join(copied)})

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
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

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
                    return self._json(save_state(
                        FILE, body.get("policy") or {},
                        body.get("classes") or []))
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
