#!/usr/bin/env bash
# 默认用户级安装；--system 使用系统级服务。配置和订阅留在仓库之外。
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo '请在 Linux 服务器上运行安装脚本。' >&2; exit 1; }
for arg in "$@"; do
  if [[ "$arg" == --system ]]; then
    exec bash "$SCRIPT_DIR/install-system.sh" "$@"
  fi
done
command -v python3 >/dev/null || { echo '需要 Python 3.10 或更新版本。' >&2; exit 1; }
PYTHON=python3
if ! "$PYTHON" -c 'import yaml' >/dev/null 2>&1; then
  VENV="$HOME/.local/share/linux-clash/venv"
  python3 -m venv "$VENV" || { echo '缺少 Python venv 支持；请管理员安装 python3-venv 后重试。' >&2; exit 1; }
  "$VENV/bin/python" -m pip install -r "$SCRIPT_DIR/requirements.txt"
  PYTHON="$VENV/bin/python"
fi
exec "$PYTHON" "$SCRIPT_DIR/linux_clash.py" install "$@"
