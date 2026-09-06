#!/bin/sh
# 同步推送到两个远端（默认 origin 与 github）。
#
# 为什么需要这个脚本：某些 NAS / 容器环境里，ssh 读的是 passwd
# 里的家目录而不是 $HOME，导致 ~/.ssh/config 那套按 Host 分流的
# 写法**根本不会被读到**；而仓库级 core.sshCommand 又会压过
# remote.<name>.sshCommand，两个平台没法各用各的密钥。
# 因此这里在调用时显式指定 GIT_SSH_COMMAND。
#
# 密钥路径可用环境变量覆盖：
#   CISRV_KEYDIR        密钥目录（默认 ~/.ssh）
#   CISRV_KEY_ORIGIN    origin 用的私钥
#   CISRV_KEY_GITHUB    github 用的私钥
# 若你的环境 ~/.ssh/config 工作正常，则不需要本脚本，
# 直接 git push 即可。
#
# 用法: tools/push-all.sh [--tags]
set -e
KEYDIR=${CISRV_KEYDIR:-$HOME/.ssh}
KEY_ORIGIN=${CISRV_KEY_ORIGIN:-$KEYDIR/id_ed25519_gitee}
KEY_GITHUB=${CISRV_KEY_GITHUB:-$KEYDIR/id_ed25519_github}
KNOWN=${CISRV_KNOWN_HOSTS:-$KEYDIR/known_hosts}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

GIT=${CISRV_GIT:-git}   # 某些套件版 git 缺 git-remote-https，可指向系统 git

if [ -n "$($GIT status --porcelain)" ]; then
    echo "✗ 工作区不干净，先提交或 stash：" >&2
    $GIT status --short >&2
    exit 1
fi

BRANCH=$($GIT rev-parse --abbrev-ref HEAD)
echo "推送分支 $BRANCH（本地 $($GIT rev-parse --short HEAD)）"

for r in origin github; do
    case $r in
        origin) KEY=$KEY_ORIGIN ;;
        github) KEY=$KEY_GITHUB ;;
    esac
    [ -f "$KEY" ] || { echo "✗ 缺密钥 $KEY" >&2; exit 1; }

    echo "── $r ──"
    GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$KNOWN" \
        $GIT push "$r" "$BRANCH"
    if [ "$1" = "--tags" ]; then
        GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$KNOWN" \
            $GIT push "$r" --tags
    fi
done

echo
echo "── 校验两端是否与本地一致 ──"
LOCAL=$($GIT rev-parse HEAD)
rc=0
for r in origin github; do
    case $r in
        origin) KEY=$KEY_ORIGIN ;;
        github) KEY=$KEY_GITHUB ;;
    esac
    REMOTE=$(GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$KNOWN" \
        $GIT ls-remote "$r" "refs/heads/$BRANCH" | cut -f1)
    if [ "$REMOTE" = "$LOCAL" ]; then
        echo "  ✓ $r $(echo "$REMOTE" | cut -c1-8)"
    else
        echo "  ✗ $r 不一致：远端 $(echo "$REMOTE" | cut -c1-8) ≠ 本地 $(echo "$LOCAL" | cut -c1-8)"
        rc=1
    fi
done
exit $rc
