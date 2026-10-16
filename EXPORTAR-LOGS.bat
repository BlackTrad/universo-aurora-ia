@echo off
setlocal
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
 .venv\Scripts\python.exe scripts\export_logs.py
) else (
 python scripts\export_logs.py
)
pause
