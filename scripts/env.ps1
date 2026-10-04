# 將所有工具環境限制在專案資料夾內（PowerShell 用）：. .\scripts\env.ps1
$Root = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = "$Root\.local\uv-cache"
$env:UV_PYTHON_INSTALL_DIR = "$Root\.local\python"
$env:UV_PYTHON_PREFERENCE = "only-managed"
$env:UV_TOOL_DIR = "$Root\.local\uv-tools"
$env:PIP_CACHE_DIR = "$Root\.local\pip-cache"
$env:PYTHONPYCACHEPREFIX = "$Root\.local\pycache"
$env:PYTHONUTF8 = "1"
$env:UV_PYTHON_INSTALL_BIN = "0"
