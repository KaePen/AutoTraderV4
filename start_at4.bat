@echo off
if "%~1"=="__run__" goto :run
cmd /c "%~f0" __run__
exit /b

:run
chcp 65001 >nul
title AutoTraderV4

cd /d "%~dp0"

echo ========================================
echo   AutoTraderV4 Starting...
echo   http://localhost:8000
echo ========================================

uv run python -m autotrader.web --auto-trade
