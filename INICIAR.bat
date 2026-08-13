@echo off
chcp 65001 >nul
title Sistema de CNDs
cd /d "%~dp0"

echo ==================================================================
echo   SISTEMA DE CONTROLE E EMISSAO DE CNDs
echo ==================================================================
echo.

REM ---------------------------------------------------------------- Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] O Python nao foi encontrado nesta maquina.
    echo.
    echo   1. Baixe em: https://www.python.org/downloads/
    echo   2. Na tela de instalacao, MARQUE a caixinha
    echo      "Add python.exe to PATH" antes de clicar em Install.
    echo   3. Reinicie o computador e clique aqui de novo.
    echo.
    pause
    exit /b 1
)

REM ------------------------------------------------- ambiente e dependencias
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Primeira execucao: preparando o ambiente. Isso leva alguns minutos...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Nao consegui criar o ambiente Python.
        pause
        exit /b 1
    )
)

call ".venv\Scripts\activate.bat"

if not exist ".venv\.instalado" (
    echo [2/3] Instalando as bibliotecas necessarias...
    python -m pip install --upgrade pip --quiet
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [ERRO] Falha ao instalar as bibliotecas.
        echo Verifique se este computador tem acesso a internet.
        pause
        exit /b 1
    )

    echo [3/3] Baixando o navegador usado pelos robos...
    python -m playwright install chromium
    if errorlevel 1 (
        echo.
        echo [AVISO] Nao consegui baixar o navegador agora.
        echo O sistema abre normalmente, mas os robos so vao funcionar
        echo depois que este comando rodar com sucesso.
        pause
    )

    echo instalado> ".venv\.instalado"
    echo.
    echo Ambiente pronto.
    echo.
)

REM ------------------------------------------------------------------ rodar
echo Iniciando o sistema...
echo O navegador abre sozinho em alguns segundos.
echo Para PARAR o sistema, feche esta janela.
echo.

cd sistema_cnd
python main.py

echo.
echo O sistema foi encerrado.
pause
