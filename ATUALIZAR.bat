@echo off
setlocal
chcp 65001 >nul 2>&1
title Sistema de CNDs - Atualizar bibliotecas
cd /d "%~dp0"

echo ==================================================================
echo   ATUALIZAR AS BIBLIOTECAS DO SISTEMA
echo ==================================================================
echo.
echo Use este arquivo quando eu enviar uma versao nova do sistema,
echo ou quando algo parar de funcionar depois de uma atualizacao.
echo.
echo Seus dados NAO sao apagados: o banco.db e a pasta de certidoes
echo continuam intactos.
echo.
pause

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo O ambiente ainda nao existe. Rode o INICIAR.bat primeiro.
    echo.
    pause
    exit /b 1
)

set "VPY=%~dp0.venv\Scripts\python.exe"

echo.
echo Atualizando bibliotecas...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt --upgrade

echo.
echo Atualizando o navegador dos robos...
"%VPY%" -m playwright install chromium

echo.
echo ==================================================================
echo   Pronto. Pode fechar esta janela e abrir o INICIAR.bat.
echo ==================================================================
pause
endlocal
