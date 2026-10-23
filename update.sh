#!/usr/bin/env bash
# 一键更新 ACM Lab 服务器部署（Docker Compose 或 systemd 原生部署）。
#
#   ./update.sh --check        # 只做无副作用预检
#   ./update.sh                # 更新到 origin/main，失败自动回滚
#   ./update.sh --mode systemd --ref origin/main
#
# 需要在本机项目目录、以能操作部署的用户运行（systemd 模式需要 root）。
# 更新器不会新建部署，也不会从网页远程执行。
set -euo pipefail

# Read-only preflight must not leave __pycache__ files in a clean deployment.
export PYTHONDONTWRITEBYTECODE=1

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd -- "$root"

find_python() {
    local candidate
    for candidate in "${ACM_UPDATE_PYTHON:-}" python3 "$root/.venv/bin/python"; do
        [ -n "$candidate" ] || continue
        if command -v "$candidate" >/dev/null 2>&1 \
            && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if ! python_bin="$(find_python)"; then
    printf '%s\n' "未找到 Python 3.10+；可设置 ACM_UPDATE_PYTHON 指向解释器" >&2
    exit 3
fi

exec "$python_bin" "$root/scripts/update.py" --project "$root" "$@"
