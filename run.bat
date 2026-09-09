@echo off
title Claude Usage Monitor
cd /d "%~dp0"
python main.py
if %errorlevel% neq 0 (
    echo.
    echo Pressione qualquer tecla para fechar...
    pause >nul
)
