@echo off
setlocal
title Meta-Bot - bridge do MetaTrader 5

rem ====================================================================
rem  Meta-Bot - bridge do MetaTrader 5 (Windows)
rem  Liga o MT5 desta maquina ao Meta-Bot. Deixe esta janela aberta.
rem  Passo a passo completo: LEIA-ME.md (mesma pasta no GitHub).
rem ====================================================================

rem 1) Cole aqui o MESMO valor de MB_MT5_BRIDGE_TOKEN da stack do Portainer:
set "MT5_BRIDGE_TOKEN=TROQUE_PELO_TOKEN_DA_STACK"

rem 2) Porta do bridge. No Meta-Bot o endereco fica http://IP-DO-TAILSCALE:8001
set "MT5_BRIDGE_PORT=8001"
set "MT5_BRIDGE_HOST=0.0.0.0"

rem 3) Opcional: caminho do terminal, se houver mais de um MT5 instalado nesta maquina
rem set "MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe"

set "BRIDGE_URL=https://raw.githubusercontent.com/dhqdev/meta-bot/main/mt5/Metatrader/bridge/metabot_bridge.py"

cd /d "%~dp0"

if not "%MT5_BRIDGE_TOKEN:TROQUE_=%"=="%MT5_BRIDGE_TOKEN%" goto :semtoken

rem --- Python 64 bits (de preferencia 3.12) ---
set "PY="
py -3.12 -c "import sys" >nul 2>nul && set "PY=py -3.12"
if not defined PY py -3.11 -c "import sys" >nul 2>nul && set "PY=py -3.11"
if not defined PY py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>nul && set "PY=python"
if not defined PY goto :sempython
%PY% -c "import sys; sys.exit(0 if sys.maxsize > 2**32 else 1)" || goto :py32

rem --- Bridge sempre na versao mais nova (se a internet falhar, usa a que ja existe) ---
echo Atualizando o bridge do Meta-Bot...
curl.exe -fsSL -o metabot_bridge.new "%BRIDGE_URL%" && move /y metabot_bridge.new metabot_bridge.py >nul
if exist metabot_bridge.new del metabot_bridge.new
if not exist metabot_bridge.py goto :semdownload

echo Instalando/atualizando o pacote MetaTrader5 no Python...
%PY% -m pip install --upgrade --disable-pip-version-check --quiet MetaTrader5
if errorlevel 1 goto :sempip

:loop
echo.
echo [%date% %time%] Bridge ligado na porta %MT5_BRIDGE_PORT%. Deixe esta janela aberta.
%PY% metabot_bridge.py
echo [%date% %time%] O bridge parou. Reiniciando em 10 segundos... Feche a janela para sair.
timeout /t 10 /nobreak >nul
goto loop

:semtoken
echo.
echo  Falta o token. Clique com o botao direito neste arquivo, escolha Editar
echo  e cole em MT5_BRIDGE_TOKEN o valor de MB_MT5_BRIDGE_TOKEN da stack.
echo.
pause
exit /b 1

:sempython
echo.
echo  Python nao encontrado. Instale o Python 3.12 de 64 bits:
echo  https://www.python.org/downloads/windows/
echo  Na instalacao, marque "Add python.exe to PATH". Depois rode este arquivo de novo.
echo.
pause
exit /b 1

:py32
echo.
echo  Este Python e de 32 bits. O pacote MetaTrader5 exige Python de 64 bits.
echo  Instale o Python 3.12 64 bits: https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:semdownload
echo.
echo  Nao consegui baixar o bridge. Confira a internet e rode de novo.
echo.
pause
exit /b 1

:sempip
echo.
echo  Falha ao instalar o pacote MetaTrader5. Use o Python 3.12 de 64 bits
echo  (versoes muito novas do Python podem ainda nao ter o pacote).
echo.
pause
exit /b 1
