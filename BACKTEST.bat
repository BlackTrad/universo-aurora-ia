@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" scripts\backtest.py --days 7
) else (
  python scripts\backtest.py --days 7
)
echo.
echo Relatorios na pasta backtest-resultados.
echo Se constar not_executed, o historico nao foi obtido.
pause
