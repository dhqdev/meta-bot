#!/bin/bash
# Inicialização do MetaTrader 5 dentro do container (roda no autostart da sessão KasmVNC).
# Baseado em https://github.com/gmag11/MetaTrader5-Docker (MIT), adaptado para o Meta-Bot:
#   - instalador do MT5 configurável (MT5_INSTALLER_URL) para usar o terminal da sua corretora;
#   - bridge HTTP do Meta-Bot (porta 8001, com token) em vez do RPyC aberto;
#   - login automático opcional (MT5_LOGIN, MT5_PASSWORD, MT5_SERVER).

export WINEPREFIX='/config/.wine'
export WINEDEBUG='-all'
wine_executable="wine"

MT5_INSTALLER_URL="${MT5_INSTALLER_URL:-https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe}"
MT5_PYTHON_PKG_VERSION="${MT5_PYTHON_PKG_VERSION:-5.0.36}"
MT5_CMD_OPTIONS="${MT5_CMD_OPTIONS:-}"
MT5_BRIDGE_PORT="${MT5_BRIDGE_PORT:-8001}"
ENABLE_MT5LINUX="${ENABLE_MT5LINUX:-false}"
MT5LINUX_PORT="${MT5LINUX_PORT:-18812}"
mono_url="https://dl.winehq.org/wine/wine-mono/10.3.0/wine-mono-10.3.0-x86.msi"
python_url="${WINE_PYTHON_URL:-https://www.python.org/ftp/python/3.9.13/python-3.9.13.exe}"
program_files="/config/.wine/drive_c/Program Files"

say() { echo "[meta-bot mt5] $*"; }

is_wine_python_package_installed() {
    $wine_executable python -c "import pkg_resources; exit(not pkg_resources.require('$1'))" 2>/dev/null
}

find_terminal() {
    find "$program_files" -maxdepth 2 -iname terminal64.exe 2>/dev/null | head -n 1
}

for dep in curl "$wine_executable"; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        say "$dep não está instalado"; exit 1
    fi
done

mkdir -p /config/.wine/drive_c

# 1) Mono (necessário para alguns instaladores)
if [ ! -e "/config/.wine/drive_c/windows/mono" ]; then
    say "[1/6] Instalando o Mono..."
    curl -fsSL -o /config/.wine/drive_c/mono.msi "$mono_url"
    WINEDLLOVERRIDES=mscoree=d $wine_executable msiexec /i /config/.wine/drive_c/mono.msi /qn
    rm -f /config/.wine/drive_c/mono.msi
else
    say "[1/6] Mono já instalado."
fi

# 2) MetaTrader 5 (o genérico da MetaQuotes entra em qualquer corretora; ou o instalador da sua)
mt5file="$(find_terminal)"
if [ -z "$mt5file" ]; then
    say "[2/6] Instalando o MetaTrader 5 de $MT5_INSTALLER_URL ..."
    $wine_executable reg add "HKEY_CURRENT_USER\\Software\\Wine" /v Version /t REG_SZ /d "win10" /f
    curl -fsSL -o /config/.wine/drive_c/mt5setup.exe "$MT5_INSTALLER_URL"
    $wine_executable "/config/.wine/drive_c/mt5setup.exe" "/auto" &
    wait
    rm -f /config/.wine/drive_c/mt5setup.exe
    mt5file="$(find_terminal)"
fi

if [ -n "$mt5file" ]; then
    say "[3/6] Abrindo o terminal: $mt5file"
    $wine_executable "$mt5file" $MT5_CMD_OPTIONS &
    if [ -z "$MT5_TERMINAL_PATH" ]; then
        MT5_TERMINAL_PATH="$(winepath -w "$mt5file" 2>/dev/null)"
        export MT5_TERMINAL_PATH
    fi
else
    say "[3/6] ERRO: terminal64.exe não encontrado depois da instalação."
fi

# 4) Python para Windows dentro do Wine (o pacote MetaTrader5 só roda no Windows)
if ! $wine_executable python --version >/dev/null 2>&1; then
    say "[4/6] Instalando o Python no Wine..."
    curl -fsSL "$python_url" -o /tmp/python-installer.exe
    $wine_executable /tmp/python-installer.exe /quiet InstallAllUsers=1 PrependPath=1
    rm -f /tmp/python-installer.exe
else
    say "[4/6] Python do Wine já instalado."
fi

# 5) Bibliotecas Python no Wine
say "[5/6] Conferindo o pacote MetaTrader5==$MT5_PYTHON_PKG_VERSION no Wine..."
if ! is_wine_python_package_installed "MetaTrader5==$MT5_PYTHON_PKG_VERSION"; then
    $wine_executable python -m pip install --upgrade --no-cache-dir pip
    $wine_executable python -m pip install --no-cache-dir "MetaTrader5==$MT5_PYTHON_PKG_VERSION"
fi

# 6) Bridge HTTP do Meta-Bot (reinicia sozinho se cair)
bridge_script="$(winepath -w /Metatrader/bridge/metabot_bridge.py 2>/dev/null)"
run_bridge() {
    while true; do
        say "[6/6] Iniciando o bridge na porta $MT5_BRIDGE_PORT"
        $wine_executable python "$bridge_script"
        say "bridge saiu (código $?); reiniciando em 10 s"
        sleep 10
    done
}
run_bridge &

# Opcional: servidor RPyC do mt5linux (sem autenticação: só habilite em rede confiável)
if [ "$ENABLE_MT5LINUX" = "true" ]; then
    say "Habilitando mt5linux (RPyC) na porta $MT5LINUX_PORT"
    if ! is_wine_python_package_installed "mt5linux"; then
        $wine_executable python -m pip install --no-cache-dir "mt5linux>=0.1.9"
    fi
    if ! python3 -c "import mt5linux" 2>/dev/null; then
        pip install --break-system-packages --no-cache-dir --no-deps mt5linux && \
        pip install --break-system-packages --no-cache-dir rpyc plumbum numpy pyxdg
    fi
    python3 -m mt5linux --host 0.0.0.0 -p "$MT5LINUX_PORT" -w $wine_executable python.exe &
fi

# Sem "wait" no fim: o autostart do Openbox precisa terminar para a sessão gráfica seguir normal.
say "Inicialização concluída. Painel VNC na porta 3000; bridge na porta $MT5_BRIDGE_PORT."
