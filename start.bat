@echo off
setlocal
cd /d "%~dp0"
title AURORA
echo AURORA - backend e painel
echo.
where py >nul 2>&1
if not errorlevel 1 (
    py -3.13 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        py -3.13 scripts\start.py
        goto finished
    )
    py -3.12 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        py -3.12 scripts\start.py
        goto finished
    )
    py -3.11 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        py -3.11 scripts\start.py
        goto finished
    )
)
where python >nul 2>&1
if not errorlevel 1 (
    python scripts\start.py
    goto finished
)
echo Python nao encontrado. Instale Python 3.13, 3.12 ou 3.11 com a opcao Add Python to PATH.
:finished
echo.
echo Inicializador encerrado. Consulte as mensagens acima.
pause
endlocal
