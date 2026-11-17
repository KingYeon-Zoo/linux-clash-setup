#!/usr/bin/env bash
# root 使用系统级 systemd，复用同一套订阅校验和更新逻辑。
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$(uname -s)" == Linux ]] || { echo '请在 Linux 服务器上运行。' >&2; exit 1; }
[[ "$EUID" == 0 ]] || { echo '系统级安装需要 root，请使用 sudo bash install.sh --system。' >&2; exit 1; }

PYTHON=''
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    PYTHON="$candidate"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo '需要 Python 3.10+。Ubuntu 可安装 python3 和 python3-venv；Alibaba Cloud Linux 3 可安装 python3.11 和 python3.11-pip。' >&2
  exit 1
fi

install -d -m 700 /opt/linux-clash
"$PYTHON" -m venv /opt/linux-clash/venv
/opt/linux-clash/venv/bin/python -m pip install -r "$SCRIPT_DIR/requirements.txt"
exec /opt/linux-clash/venv/bin/python "$SCRIPT_DIR/linux_clash.py" install --system "$@"
