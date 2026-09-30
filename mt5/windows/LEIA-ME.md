# MetaTrader 5 no Windows ligado ao Meta-Bot

Toda a inteligência fica no servidor do Meta-Bot: agentes, estratégias, risco, IA e decisões. Na máquina do MetaTrader 5 roda só o **bridge**, um programinha em Python que recebe as ordens do Meta-Bot e as executa no MT5, além de devolver cotações, candles, saldo e posições.

```
Meta-Bot (servidor, trade.tekvosoft.com)
        │  HTTP com token, dentro da rede privada do Tailscale
        ▼
Bridge do Meta-Bot (porta 8001)  ──►  MetaTrader 5  ──►  sua corretora
        └──────────── PC ou VPS Windows ────────────┘
```

## O que você precisa

- Um **PC ou VPS Windows** 10/11 ou Server, com 2 GB de RAM ou mais, **sempre ligado** durante o horário de mercado.
- O **MetaTrader 5 da sua corretora** e a conta: comece pela **demo**.
- O **token** da stack: o valor de `MB_MT5_BRIDGE_TOKEN`.
- Uma conta grátis no **[Tailscale](https://tailscale.com)**, que cria a rede privada entre o servidor e o Windows.

## 1. MetaTrader 5

1. Instale o MT5 pelo site da sua corretora ou pela [MetaQuotes](https://www.metatrader5.com/pt/download).
2. Entre na conta com a **senha principal**. A senha de investidor é só de leitura e não deixa operar.
3. Em **Ferramentas → Opções → Expert Advisors**:
   - marque **Permitir negociação algorítmica**;
   - desmarque as opções "Desabilitar negociação algorítmica quando…" (troca de conta, perfil etc.).
4. Confira se o botão **Algo Trading** da barra de ferramentas está **verde**.

Não precisa colocar os ativos na "Observação do Mercado": o bridge adiciona sozinho os que o Meta-Bot usar.

## 2. Python

Instale o **Python 3.12 de 64 bits** ([python.org/downloads/windows](https://www.python.org/downloads/windows/), "Windows installer (64-bit)"). Na primeira tela da instalação, marque **Add python.exe to PATH**.

## 3. Bridge do Meta-Bot

1. Crie a pasta `C:\MetaBot`.
2. Baixe o [`iniciar-bridge.bat`](https://raw.githubusercontent.com/dhqdev/meta-bot/main/mt5/windows/iniciar-bridge.bat) para essa pasta (botão direito no link → Salvar link como).
3. Clique com o botão direito no arquivo → **Editar** e troque `TROQUE_PELO_TOKEN_DA_STACK` pelo valor de `MB_MT5_BRIDGE_TOKEN` da stack. Salve.
4. Com o **MT5 aberto e logado**, dê dois cliques no `iniciar-bridge.bat`. Na primeira vez ele baixa o bridge e instala o pacote `MetaTrader5`. Depois deve aparecer:
   ```
   Meta-Bot MT5 bridge 1.0.0 ouvindo em 0.0.0.0:8001
   terminal MT5 conectado
   ```
   Deixe essa janela aberta. Se ela fechar ou o bridge cair, o `.bat` reinicia sozinho.

## 4. Rede privada (Tailscale)

1. **No Windows:** instale o [Tailscale](https://tailscale.com/download/windows) e entre com a sua conta. Anote o IP que começa com **100.** (ícone do Tailscale → *My devices*).
2. **No servidor do Meta-Bot** (terminal/SSH), com a **mesma conta** do Tailscale:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   ```
3. **Liberar a porta 8001 só para a rede do Tailscale:** no Windows, abra o **PowerShell como administrador** e rode:
   ```powershell
   New-NetFirewallRule -DisplayName "Meta-Bot bridge" -Direction Inbound -Protocol TCP -LocalPort 8001 -RemoteAddress 100.64.0.0/10 -Action Allow
   ```
4. **Testar pelo servidor:**
   ```bash
   curl http://100.x.y.z:8001/ping
   ```
   Troque pelo IP do Windows. A resposta deve ter `"ok": true`.

## 5. Ligar no Meta-Bot

Escolha um dos dois:

- **Pela stack:** em `MB_MT5_BRIDGE_URL`, coloque `http://100.x.y.z:8001` e atualize a stack (*Update the stack*). O terminal "MT5 principal" passa a apontar para o Windows.
- **Pela tela:** em **Config. → MetaTrader 5**, edite o terminal ou clique em *Ligar um MetaTrader 5*. Preencha o endereço `http://100.x.y.z:8001` e o token, marque como ativo e clique em **Testar**.

Com o MT5 ligado, o topo da tela mostra "MT5 conectado" e a equipe passa a usar os preços da sua corretora. Aí:

1. Em **Config. → Ativos e tempos gráficos**, escolha os ativos com o **nome exato da corretora**. A busca lista os do MT5, por exemplo `EURUSD`, `EURUSDm`, `WIN$N` ou `WDO$N`.
2. Deixe rodar um tempo no modo **simulado**: preços reais, ordens de mentira.
3. Para enviar **ordens de verdade**, vá em **Config. → Modo de operação → Usar a conta do MT5** (pede sua senha). As ordens do Meta-Bot levam o *magic number* 770077, e o sistema só mexe nas posições dele, nunca nas que você abrir na mão.

## Deixar sempre ligado

- Aperte **Win+R**, digite `shell:startup` e cole ali um **atalho do MT5** e um **atalho do `iniciar-bridge.bat`**.
- Desative a suspensão do Windows em *Energia → Nunca*.
- Numa VPS, configure o logon automático do Windows. O bridge precisa rodar na **mesma sessão de usuário** do MT5, então não use serviço do Windows.
- Agende as atualizações do Windows para fora do horário de mercado.

## Várias corretoras ao mesmo tempo

Use um MT5 e um bridge para cada conta:
1. Instale cada MT5 numa pasta diferente.
2. Copie o `.bat`, mudando `MT5_BRIDGE_PORT` (8002, 8003…) e `MT5_TERMINAL_PATH` para o `terminal64.exe` daquela instalação.
3. Libere cada porta no firewall.
4. Cadastre cada terminal no Meta-Bot. O terminal marcado como **ativo** é o que o sistema usa.

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| "token do bridge recusado" no Meta-Bot | O token do `.bat` está diferente do `MB_MT5_BRIDGE_TOKEN` da stack. |
| "sem conexão" ou timeout no *Testar* | Confira se o `.bat` está aberto, se o Tailscale está ligado nos dois lados, a regra do firewall e o `curl .../ping` pelo servidor. |
| Ordem recusada com código **10027** | O **Algo Trading** está desligado no MT5 (botão tem que ficar verde). |
| Ordem recusada com código **10017** ou "trade disabled" | A conta entrou com a senha de investidor, ou a corretora não liberou negociação nesse ativo. |
| "terminal ainda não disponível" no bridge | O MT5 está fechado ou não logado. Abra, entre na conta e aguarde. |
| O ativo não aparece | O nome é diferente nessa corretora. Busque em Config. → Ativos e tempos gráficos. |

**Alternativa sem Windows:** numa máquina Linux Intel/AMD com Docker, use o [`deploy/mt5-remoto.yml`](../../deploy/mt5-remoto.yml). Ele sobe o MT5 no Wine com o bridge já incluso e um painel web.
