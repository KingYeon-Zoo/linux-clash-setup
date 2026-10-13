#!/usr/bin/env bash
# 用户级安装；配置和订阅始终留在仓库之外。
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo '请在 Linux 服务器上运行安装脚本。' >&2; exit 1; }
command -v python3 >/dev/null || { echo '需要 Python 3.10 或更新版本。' >&2; exit 1; }
PYTHON=python3
if ! "$PYTHON" -c 'import yaml' >/dev/null 2>&1; then
  VENV="$HOME/.local/share/linux-clash/venv"
  python3 -m venv "$VENV" || { echo '缺少 Python venv 支持；请管理员安装 python3-venv 后重试。' >&2; exit 1; }
  "$VENV/bin/python" -m pip install -r "$ROOT/requirements.txt"
  PYTHON="$VENV/bin/python"
fi
exec "$PYTHON" "$ROOT/linux_clash.py" install "$@"
