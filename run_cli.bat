@echo off
title Claude Usage Monitor (Terminal)
cd /d "%~dp0"
python main.py --cli
if %errorlevel% neq 0 (
    echo.
    echo Pressione qualquer tecla para fechar...
    pause >nul
)
