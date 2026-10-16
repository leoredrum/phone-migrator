@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 未找到 python，请先安装 Python 3.11+ 并勾选 "Add to PATH"
  pause
  exit /b 1
)
python main.py
if errorlevel 1 pause
