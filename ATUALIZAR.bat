@echo off
chcp 65001 >nul
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
    echo Ambiente ainda nao existe. Rode o INICIAR.bat primeiro.
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo.
echo Atualizando bibliotecas...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --upgrade

echo.
echo Atualizando o navegador dos robos...
python -m playwright install chromium

echo.
echo ==================================================================
echo   Pronto. Pode fechar esta janela e abrir o INICIAR.bat.
echo ==================================================================
pause
