@echo off
title Freq Planner 2G
cd /d "%~dp0"
echo ========================================================
echo   Iniciando Freq Planner 2G...
echo ========================================================
python main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Ocurrio un error al ejecutar la aplicacion.
    pause
)
