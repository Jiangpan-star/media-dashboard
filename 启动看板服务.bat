@echo off
chcp 65001 >nul
title 融媒体数据看板 · 本地服务
echo.
echo  ╔══════════════════════════════════════════╗
echo  ║   融媒体数据看板 · 启动中...              ║
echo  ╚══════════════════════════════════════════╝
echo.
echo  服务启动后请在浏览器访问：
echo  http://localhost:8765
echo.
echo  按 Ctrl+C 可停止服务
echo.

cd /d "%~dp0"
python app.py

echo.
echo  服务已停止，按任意键关闭...
pause >nul
