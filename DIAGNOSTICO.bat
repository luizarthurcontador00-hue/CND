@echo off
setlocal
chcp 65001 >nul 2>&1
title Sistema de CNDs - Diagnostico
cd /d "%~dp0"

set "REL=%~dp0diagnostico.txt"

echo ==================================================================
echo   DIAGNOSTICO DO SISTEMA DE CNDs
echo ==================================================================
echo.
echo Este arquivo NAO altera nada. Ele so junta informacoes para eu
echo descobrir por que o sistema nao esta abrindo.
echo.

echo ===== DIAGNOSTICO EM %DATE% %TIME% =====> "%REL%"
echo.>> "%REL%"

echo --- Pasta atual --->> "%REL%"
echo %CD% >> "%REL%"
echo.>> "%REL%"

echo --- Windows --->> "%REL%"
ver >> "%REL%" 2>&1
echo.>> "%REL%"

echo --- Arquivos que deveriam existir --->> "%REL%"
if exist "INICIAR.bat"          (echo OK   INICIAR.bat>> "%REL%")          else (echo FALTA INICIAR.bat>> "%REL%")
if exist "requirements.txt"     (echo OK   requirements.txt>> "%REL%")     else (echo FALTA requirements.txt>> "%REL%")
if exist "sistema_cnd\main.py"  (echo OK   sistema_cnd\main.py>> "%REL%")  else (echo FALTA sistema_cnd\main.py>> "%REL%")
if exist "sistema_cnd\config.yaml" (echo OK   sistema_cnd\config.yaml>> "%REL%") else (echo FALTA sistema_cnd\config.yaml>> "%REL%")
if exist ".venv\Scripts\python.exe" (echo OK   ambiente .venv ja criado>> "%REL%") else (echo --   ambiente .venv ainda nao criado>> "%REL%")
echo.>> "%REL%"

echo --- Conteudo da pasta --->> "%REL%"
dir /b >> "%REL%" 2>&1
echo.>> "%REL%"

echo --- Python --->> "%REL%"
echo [py -3]>> "%REL%"
py -3 --version >> "%REL%" 2>&1
echo [python]>> "%REL%"
python --version >> "%REL%" 2>&1
echo [python3]>> "%REL%"
python3 --version >> "%REL%" 2>&1
echo [onde esta]>> "%REL%"
where python >> "%REL%" 2>&1
where py >> "%REL%" 2>&1
echo.>> "%REL%"

echo --- Log da ultima execucao --->> "%REL%"
if exist "ultima_execucao.log" (type "ultima_execucao.log" >> "%REL%" 2>&1) else (echo sem log ainda>> "%REL%")
echo.>> "%REL%"

echo --- Log do sistema --->> "%REL%"
if exist "sistema_cnd\logs\sistema.log" (
    powershell -NoProfile -Command "Get-Content 'sistema_cnd\logs\sistema.log' -Tail 40" >> "%REL%" 2>&1
) else (
    echo sem log do sistema ainda>> "%REL%"
)

echo Pronto.
echo.
echo Foi criado o arquivo:
echo     %REL%
echo.
echo Abra ele e me mande o conteudo. Com isso eu descubro o problema.
echo.
notepad "%REL%"
echo.
pause
endlocal
