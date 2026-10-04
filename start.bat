@echo off
rem 啟動二遊全勤君（使用專案內虛擬環境）
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONPATH=%~dp0src
set PYTHONUTF8=1
set PYTHONPYCACHEPREFIX=%~dp0.local\pycache
start "" "%~dp0.venv\Scripts\pythonw.exe" -m gachahub %*
