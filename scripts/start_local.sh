#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ! -x .venv/bin/acm-web ]]; then
  echo "请先创建 .venv 并安装项目依赖，详见 README.md。" >&2
  exit 1
fi
echo "ACM Lab：http://127.0.0.1:8000（按 Ctrl+C 停止）"
exec .venv/bin/acm-web "$@"
