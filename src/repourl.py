#!/usr/bin/env python3
"""
repourl.py — 仓库地址推导（纯函数，无副作用，方便单测）

网页首次使用向导里，用户只会粘贴一个仓库地址（可能是网页地址、HTTPS clone
地址、SSH 地址，结尾可能带 .git、可能带 /raw/...）。本模块把各种写法
统一成：

    · clone_url   git clone 用的 HTTPS 地址
    · web_url     浏览器打开的仓库主页
    · raw_base(branch)  静态文件公开根地址（schedule.publish.base_url）
    · platform    "gitee" | "github" | "other"

raw 地址为什么重要：ClassIsland 客户端是不带任何凭据去 GET 这些 JSON 的，
所以**存放配置的仓库必须是公开仓库**（或用其他公开托管），否则大屏拉不到。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RepoId:
    platform: str          # "gitee" | "github" | "other"
    host: str              # 例如 gitee.com；本地仓库为 ""
    owner: str
    repo: str              # 不带 .git
    local_path: str = ""   # 仅本地仓库：原始路径（clone 用 file://）

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}"


_SSH_RE = re.compile(r"^git@(?P<host>[^:]+):(?P<slug>.+?)(?:\.git)?/?$")


def parse_repo(text: str) -> RepoId:
    """把各种仓库写法解析成 RepoId；无法识别抛 ValueError。"""
    s = (text or "").strip()
    if not s:
        raise ValueError("仓库地址为空")

    # 本地仓库（file:// 或直接给绝对/相对路径）：向导/测试/内网自建都可能用到
    if s.startswith("file://") or s.startswith("/") or (
            len(s) > 2 and s[1] == ":" and "\\" in s):
        p = s[len("file://"):] if s.startswith("file://") else s
        p = p.rstrip("/")
        if not p:
            raise ValueError("本地仓库路径为空")
        parts = [x for x in re.split(r"[/\\]", p) if x]
        if len(parts) < 1:
            raise ValueError(f"无法解析本地仓库路径：{text!r}")
        name = parts[-1][:-4] if parts[-1].endswith(".git") else parts[-1]
        owner = parts[-2] if len(parts) >= 2 else "local"
        return RepoId(platform="other", host="", owner=owner, repo=name,
                      local_path=p)

    m = _SSH_RE.match(s)
    if m:
        host = m.group("host").lower()
        slug = m.group("slug")
    else:
        if "://" not in s:
            s = "https://" + s
        u = urlsplit(s)
        host = (u.hostname or "").lower()
        slug = u.path or ""
        if not host or not slug:
            raise ValueError(f"无法解析仓库地址：{text!r}")

    # 去掉前后斜杠、.git，以及网页上复制进来时可能带的子路径
    # （/raw/master、/tree/main、/blob/... 等），只保留 owner/repo 两段。
    slug = slug.strip("/")
    if slug.endswith(".git"):
        slug = slug[:-4]
    for tail in ("/raw", "/tree", "/blob", "/commits", "/releases",
                 "/pulls", "/issues"):
        idx = slug.find(tail + "/")
        if idx != -1:
            slug = slug[:idx]
    slug = slug.strip("/")

    parts = [p for p in slug.split("/") if p and p not in (".", "..")]
    if len(parts) < 2:
        raise ValueError(f"地址里缺少「用户名/仓库名」：{text!r}")
    owner, repo = parts[0], parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", owner) or \
            not re.fullmatch(r"[A-Za-z0-9_.\-]+", repo):
        raise ValueError(f"仓库地址含非法字符：{text!r}")

    if host.endswith("gitee.com"):
        platform = "gitee"
    elif host.endswith("github.com") or host.endswith("githubusercontent.com"):
        platform = "github"
    else:
        platform = "other"
    return RepoId(platform=platform, host=host, owner=owner, repo=repo)


def clone_url(rid: RepoId) -> str:
    if rid.local_path:
        return "file://" + rid.local_path
    return f"https://{rid.host}/{rid.owner}/{rid.repo}.git"


def web_url(rid: RepoId) -> str:
    if rid.local_path:
        return rid.local_path
    return f"https://{rid.host}/{rid.owner}/{rid.repo}"


def raw_base(rid: RepoId, branch: str) -> str:
    """各平台的 raw 根地址。other 平台给不出确定规则，交回报错由调用方手填。"""
    b = (branch or "").strip()
    if not b:
        raise ValueError("缺少分支名")
    if rid.platform == "gitee":
        # Gitee raw 会 302 到 raw.giteeusercontent.com，客户端/请求方需跟随
        return f"https://{rid.host}/{rid.owner}/{rid.repo}/raw/{b}"
    if rid.platform == "github":
        return f"https://raw.githubusercontent.com/{rid.owner}/{rid.repo}/{b}"
    raise ValueError(
        "暂不认识的代码托管平台，请在「基本信息」里手工填写 raw 根地址")


_RAW_GITEE = re.compile(
    r"^https?://gitee\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/raw/(?P<branch>[^/?#]+)")
_RAW_GITHUB = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<branch>[^/?#]+)")


def repo_web_from_raw(base_url: str) -> str | None:
    """从 raw 发布根地址反推出仓库主页（HTTPS clone 用）。

    支持 Gitee（gitee.com/owner/repo/raw/branch）与 GitHub
    （raw.githubusercontent.com/owner/repo/branch）。认不出来返回 None。
    """
    s = (base_url or "").strip().rstrip("/")
    m = _RAW_GITEE.match(s)
    if m:
        return f"https://gitee.com/{m['owner']}/{m['repo']}.git"
    m = _RAW_GITHUB.match(s)
    if m:
        return f"https://github.com/{m['owner']}/{m['repo']}.git"
    return None
