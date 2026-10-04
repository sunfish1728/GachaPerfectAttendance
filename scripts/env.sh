# 將所有工具環境限制在專案資料夾內（Git Bash 用）：source scripts/env.sh
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export UV_CACHE_DIR="$ROOT/.local/uv-cache"
export UV_PYTHON_INSTALL_DIR="$ROOT/.local/python"
export UV_PYTHON_PREFERENCE=only-managed
export UV_TOOL_DIR="$ROOT/.local/uv-tools"
export PIP_CACHE_DIR="$ROOT/.local/pip-cache"
export PYTHONPYCACHEPREFIX="$ROOT/.local/pycache"
export PYTHONUTF8=1
export UV_PYTHON_INSTALL_BIN=0
