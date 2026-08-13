@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
title Sistema de CNDs
cd /d "%~dp0"

set "LOG=%~dp0ultima_execucao.log"
echo ===== Sistema de CNDs - inicio em %DATE% %TIME% =====> "%LOG%"

echo ==================================================================
echo   SISTEMA DE CONTROLE E EMISSAO DE CNDs
echo ==================================================================
echo.
echo Se algo der errado, o arquivo ultima_execucao.log guarda o motivo.
echo.

REM ------------------------------------------------ a pasta esta completa?
if not exist "sistema_cnd\main.py" (
    echo [ERRO] Nao encontrei a pasta "sistema_cnd" aqui do lado.
    echo.
    echo Isso quase sempre quer dizer que o ZIP foi aberto pela metade,
    echo ou que este arquivo foi copiado sozinho para outra pasta.
    echo.
    echo Esta pasta precisa conter, lado a lado:
    echo     INICIAR.bat
    echo     requirements.txt
    echo     sistema_cnd\   ^(com o main.py dentro^)
    echo.
    echo Pasta atual: %CD%
    echo [ERRO] pasta sistema_cnd nao encontrada em %CD% >> "%LOG%"
    goto :fim
)

REM ------------------------------------------------------------ Python
REM O "py" e o lancador oficial do Windows e e o mais confiavel.
REM O "python" as vezes abre a Loja da Microsoft em vez de rodar.
set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
    python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
    python3 --version >nul 2>&1 && set "PY=python3"
)

if not defined PY (
    echo [ERRO] O Python nao foi encontrado nesta maquina.
    echo.
    echo Como resolver:
    echo   1^) Acesse https://www.python.org/downloads/
    echo   2^) Baixe e abra o instalador.
    echo   3^) MARQUE a caixinha "Add python.exe to PATH" na primeira tela.
    echo      Sem isso nao funciona.
    echo   4^) Conclua a instalacao, reinicie o computador
    echo      e clique neste arquivo de novo.
    echo [ERRO] Python nao encontrado >> "%LOG%"
    goto :fim
)

for /f "delims=" %%v in ('%PY% --version 2^>^&1') do set "VERSAO=%%v"
echo Python encontrado: !VERSAO!   ^(comando: %PY%^)
echo Python: !VERSAO! via %PY% >> "%LOG%"
echo.

REM ------------------------------------------- ambiente e dependencias
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Primeira execucao: preparando o ambiente.
    echo       Isso leva alguns minutos e acontece uma vez so...
    %PY% -m venv .venv >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo.
        echo [ERRO] Nao consegui criar o ambiente Python.
        echo Veja o detalhe em ultima_execucao.log
        goto :fim
    )
)

set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo [ERRO] O ambiente foi criado mas o python dele nao apareceu.
    echo Apague a pasta .venv e rode este arquivo de novo.
    echo [ERRO] .venv sem python.exe >> "%LOG%"
    goto :fim
)

if not exist ".venv\.instalado" (
    echo [2/3] Instalando as bibliotecas necessarias...
    "%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
    "%VPY%" -m pip install --prefer-binary -r requirements.txt >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo.
        echo [ERRO] Falha ao instalar as bibliotecas.
        echo.
        echo Duas causas possiveis:
        echo.
        echo  1^) Sem acesso a internet neste computador.
        echo.
        echo  2^) Seu Python e MUITO novo e alguma biblioteca ainda nao tem
        echo     pacote pronto para ele. O sinal disso e a frase
        echo     "Microsoft Visual C++ 14.0 or greater is required"
        echo     dentro do ultima_execucao.log.
        echo     Nesse caso, o caminho mais simples e instalar o Python 3.12
        echo     ou 3.13 em https://www.python.org/downloads/
        echo     ^(NAO precisa desinstalar o que voce ja tem^), apagar a pasta
        echo     .venv e clicar neste arquivo de novo.
        echo.
        echo O detalhe completo do erro esta em ultima_execucao.log
        goto :fim
    )

    echo [3/3] Baixando o navegador usado pelos robos...
    "%VPY%" -m playwright install chromium >> "%LOG%" 2>&1
    if errorlevel 1 (
        echo.
        echo [AVISO] Nao consegui baixar o navegador agora.
        echo O sistema abre normalmente e as certidoes CNDT e SEFAZ-GO
        echo funcionam assim mesmo. FGTS e Federal precisam do navegador.
        echo.
    )

    echo instalado > ".venv\.instalado"
    echo.
    echo Ambiente pronto.
    echo.
)

REM ------------------------------------------------------------ rodar
echo Iniciando o sistema...
echo O navegador abre sozinho em alguns segundos.
echo Se nao abrir, digite no navegador:  http://localhost:8000
echo.
echo Para PARAR o sistema, feche esta janela.
echo.

cd sistema_cnd
"%VPY%" main.py
set "SAIDA=%ERRORLEVEL%"
cd ..

echo.
if not "%SAIDA%"=="0" (
    echo O sistema encerrou com erro ^(codigo %SAIDA%^).
    echo Detalhes em sistema_cnd\logs\sistema.log
    echo saida do main.py: %SAIDA% >> "%LOG%"
) else (
    echo O sistema foi encerrado.
)

:fim
echo.
echo ==================================================================
echo   Aperte uma tecla para fechar esta janela.
echo ==================================================================
pause >nul
endlocal
