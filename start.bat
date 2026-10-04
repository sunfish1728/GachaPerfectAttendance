@echo off
rem 啟動二遊全勤君：安裝版使用 python\（程式包內的 Python），開發環境使用 .venv\
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
if exist "%~dp0python\pythonw.exe" (
    start "" "%~dp0python\pythonw.exe" -m gachahub %*
) else (
    set PYTHONPATH=%~dp0src
    set PYTHONPYCACHEPREFIX=%~dp0.local\pycache
    start "" "%~dp0.venv\Scripts\pythonw.exe" -m gachahub %*
)
