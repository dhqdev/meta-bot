# Meta-Bot

**Um escritório de trading com oito agentes que trabalham juntos, aprendem com o resultado real e operam no MetaTrader 5 de qualquer corretora.** Você acompanha tudo numa sala em pixel-art, vista de cima, com cada agente na sua mesa, andando até a sala do gerente para as reuniões e comemorando quando uma operação fecha no lucro.

![Escritório do Meta-Bot](docs/img/escritorio.png)

- **Agentes especializados**: notícias, horários e calendário, estratégias, gerente, risco, caixa, auditoria e TI.
- **Estratégias testadas de verdade**: 18 setups (Vilela One, IndicatorSpot, setups brasileiros e clássicos), backtest com custos e validação fora da amostra, ranking pela taxa de acerto.
- **IA onde ajuda, regras onde precisa**: notícias, gerente e auditoria usam IA (OpenRouter ou Claude); estratégias, risco e caixa são 100% determinísticos.
- **Skills que evoluem**: cada agente ganha XP e sobe de nível; a Estrategista evolui parâmetros; a Auditora registra lições que entram no prompt dos colegas.
- **MetaTrader 5 de qualquer corretora**, num PC ou VPS Windows (ou em Docker numa máquina Intel/AMD). O Meta-Bot fala com ele por um bridge com token, e toda a inteligência fica no servidor.
- **Pronto para o Portainer** (Docker Swarm + Traefik) em `trade.tekvosoft.com`, com imagens publicadas pelo GitHub Actions.
- **Funciona no celular**: o escritório e todas as telas se adaptam à tela pequena.

> ⚠️ Trading envolve risco de perda. O sistema começa no **modo simulado**; teste em conta **demo** antes de ligar numa conta real.

---

## Sumário

1. [A equipe](#a-equipe)
2. [Como uma operação acontece](#como-uma-operação-acontece)
3. [Skills que evoluem](#skills-que-evoluem)
4. [Estratégias e backtest](#estratégias-e-backtest)
5. [Inteligência artificial (OpenRouter e Claude)](#inteligência-artificial-openrouter-e-claude)
6. [MetaTrader 5: qualquer corretora](#metatrader-5-qualquer-corretora)
7. [Subir no Portainer (trade.tekvosoft.com)](#subir-no-portainer-tradetekvosoftcom)
8. [Rodar na sua máquina](#rodar-na-sua-máquina)
9. [Segurança](#segurança)
10. [Configurações](#configurações)
11. [Estrutura do projeto e API](#estrutura-do-projeto-e-api)
12. [Testes e CI](#testes-e-ci)
13. [Créditos](#créditos)

---

## A equipe

| Agente | Função | IA? | O que faz e o que aprende |
|---|---|---|---|
| **Tito** | TI e Dados | não | Mantém o MT5 conectado (qualquer corretora), faz login pelo bridge, estima o fuso do servidor, aquece o cache de candles e faz a manutenção do banco. |
| **Nina** | Notícias | **sim** | Lê RSS de FXStreet, Investing, CNBC, MarketWatch, Yahoo, CoinDesk, InfoMoney, Money Times e Investing Brasil. Classifica na hora por palavras-chave e, em lotes, com IA (impacto, categoria e sentimento por ativo). Depois confere se a notícia acertou a direção do preço e dá mais peso às fontes que acertam. |
| **Hugo** | Horários e Calendário | não | Mapeia os melhores horários de cada ativo (volatilidade e resultado por hora), acompanha o calendário econômico (Forex Factory) e pausa entradas perto de notícias de alto impacto. |
| **Estela** | Estrategista | não | Roda os backtests de todas as estratégias em cada ativo e tempo gráfico que você escolher, ranqueia pela taxa de acerto (ou outro critério), aprova só o que se sustenta fora da amostra e evolui os parâmetros com validação. Gera os sinais em tempo real. |
| **Gustavo** | Gerente | **sim** | Junta tudo (backtest, hora, notícias, calendário, resultado real e risco) e decide **qual ativo, qual estratégia e em qual horário** a mesa opera. Faz reuniões com a equipe na sala dele e aprende quanto confiar em cada colega. |
| **Rita** | Risco | não | Calcula o lote pelo risco por operação, limita perda diária, drawdown, exposição por moeda e spread anormal. Tem o *kill switch*. Fica mais conservadora depois de perdas. |
| **Caio** | Caixa | não | Envia as ordens, coloca **stop loss e stop gain**, faz break-even, trailing, saída por tempo, fecha antes do fim de semana e antes do fechamento da B3. Acompanha cada posição até o fim e registra o resultado. |
| **Aurora** | Auditoria | **sim** (opcional) | Compara o resultado real com o prometido no backtest, põe estratégias "em observação" quando caem, distribui XP e escreve o diário do dia às 22h com lições para a equipe. |

A tela **Agentes** mostra as skills, o nível e o histórico de cada um; dá para mandar um agente executar uma tarefa na hora.

![Agentes](docs/img/agentes.png)

<details><summary>No celular</summary>

<img src="docs/img/celular.png" alt="Meta-Bot no celular" width="300">

</details>

## Como uma operação acontece

```mermaid
flowchart LR
    N[Nina<br/>notícias] --> G
    H[Hugo<br/>horários e calendário] --> G
    E[Estela<br/>backtests e ranking] --> G
    A[Aurora<br/>resultado real e lições] --> G
    G[Gustavo<br/>plano: ativo, estratégia, horário] --> S{Sinal da Estela<br/>num setup do plano}
    S --> G2[Gustavo revisa<br/>notícias e calendário]
    G2 --> R[Rita<br/>lote e limites]
    R --> C[Caio<br/>ordem, SL/TP, trailing]
    C --> A
```

1. A cada 15 minutos o **Gustavo** monta o plano com até 3 setups (ativo + tempo gráfico + estratégia + direção). Com IA, ele escolhe entre candidatos que as regras já filtraram; sem IA, usa a pontuação da equipe.
2. Quando fecha um candle, a **Estela** confere os sinais dos setups do plano.
3. O **Gustavo** revisa o sinal contra notícias e calendário; a **Rita** calcula o lote e confere os limites; o **Caio** executa com stop e alvo.
4. Quando a operação fecha, a **Aurora** atualiza as estatísticas reais, distribui XP e ajusta a confiança do gerente em cada colega.

No modo **simulado**, o Caio usa um corretor de papel com spread, slippage e comissão. No modo **conta da corretora**, as ordens vão para o MT5 (exige senha, confirmação e MT5 conectado).

## Skills que evoluem

- **XP e níveis**: cada skill sobe de nível (1 a 10) com trabalho e, principalmente, com resultado real — backtest aprovado, evolução confirmada fora da amostra, notícia que acertou a direção, operação bem gerida.
- **Evolução de estratégias** (Estela, a cada 24 h): gera variações (parâmetros, filtros, stop e alvo) das melhores estratégias, testa com *walk-forward* (70% para escolher, 30% fora da amostra) e só aceita a nova versão se ela melhorar a taxa de acerto sem perder a expectativa. Cada estratégia tem versões (`v1`, `v2`…).
- **Revalidação**: estratégia que vai mal no real entra "em observação" e é retestada antes de voltar ao plano.
- **Gestão de saída adaptativa** (Caio): aprende break-even e trailing que funcionam em cada setup.
- **Risco adaptativo** (Rita): reduz o risco depois de sequência de perdas e volta aos poucos.
- **Confiança na equipe** (Gustavo): pesos multiplicativos para Estela, Hugo, Nina e resultado real, ajustados a cada operação fechada.
- **Lições e playbooks**: a Aurora grava lições; elas entram, junto com o playbook da função (`backend/app/agents/playbooks/`), no prompt da Nina e do Gustavo.

## Estratégias e backtest

![Ranking de estratégias](docs/img/estrategias.png)

| Origem | Estratégias |
|---|---|
| **Vilela One** | IFR (RSI) Reversão · Ichimoku (4 regras) · Rompimento de Keltner · MACD Histograma · Cruzamento de Médias · Bandas de Bollinger (reversão) · Price Action: Engolfo |
| **IndicatorSpot** | Half Trend · ADX Buy/Sell (DMI) · Super Signals (Supertrend) · NRTR (reversão por trailing) · Guppy (GMMA) |
| **Setups brasileiros** | HiLo Activator · Setup 9.1 (Larry Williams, com ordem stop) · Agulhada do Didi |
| **Clássicos** | Rompimento Donchian (Tartarugas) · Squeeze (compressão e rompimento) · Estocástico (cruzamento nos extremos) |

Filtros combináveis: tendência (EMA 200), força (ADX ≥ 20), volatilidade normal, volume acima da média, IFR confirma e melhores horários.

**Um backtest honesto:**
- sinal no fechamento do candle, entrada na **abertura do próximo** (sem olhar o futuro — há testes que garantem isso para todas as estratégias);
- compra no *ask*, venda no *bid* (spread), slippage e comissão por lote;
- se stop e alvo cabem no mesmo candle, conta o **stop** (pior caso);
- stop e alvo por ATR, break-even, trailing e saída por tempo iguais aos do Caio;
- métricas: taxa de acerto, **acerto mínimo (limite inferior de Wilson, 95%)**, fator de lucro, expectativa em R, queda máxima, resultado por hora;
- **aprovação**: mínimo de 25 operações, fator de lucro ≥ 1,1 e desempenho positivo nos 30% finais que não foram usados para escolher.

Na tela **Estratégias** há o ranking (filtros por ativo e tempo gráfico), o backtest sob demanda com gráfico e o catálogo com a explicação de cada estratégia.

## Inteligência artificial (OpenRouter e Claude)

A IA é opcional: sem chave, todos os agentes funcionam só com as regras. Com uma chave, **Nina, Gustavo e Aurora** passam a usar IA.

### OpenRouter (recomendado, econômico)

Cole a chave do [OpenRouter](https://openrouter.ai/keys) na stack (`MB_OPENROUTER_API_KEY`) ou em **Config. → Inteligência artificial**. Os modelos de cada agente **já vêm escolhidos pensando em economia**, porque o sistema fica ligado o dia todo:

| Agente | Modelo predefinido | Por quê | Preço aprox. (US$ por milhão de tokens, entrada/saída) |
|---|---|---|---|
| Nina (notícias) | `deepseek/deepseek-v4-flash` | é quem mais chama a IA; tarefa simples de classificação | 0,09 / 0,18 |
| Gustavo (gerente) | `google/gemini-3.1-flash-lite` | escolhe entre candidatos já filtrados pelas regras; rápido e barato | 0,25 / 1,50 |
| Aurora (auditora) | `anthropic/claude-haiku-4.5` | escreve diário e lições **uma vez por dia**; melhor texto, custo irrisório | 1,00 / 5,00 |
| Reserva | `google/gemini-3.1-flash-lite` | usado se o modelo principal estiver fora do ar ou sumir do catálogo | 0,25 / 1,50 |

Economias ligadas por padrão:
- a Nina junta as manchetes novas e chama a IA no máximo **a cada 15 min**;
- o Gustavo **reaproveita o plano da IA** enquanto os candidatos não mudam (validade de 60 min);
- raciocínio curto só para gerente e auditora (nunca nas notícias) e respostas limitadas a 8 mil tokens;
- **limite de 12 chamadas por hora** e **orçamento de US$ 0,50 por dia** (passou disso, os agentes seguem só com as regras até virar o dia);
- o custo real de cada chamada vem na resposta do OpenRouter e aparece na tela (hoje, 30 dias e por agente), junto com uma estimativa máxima por dia e por mês.

Na estimativa padrão, o teto fica em torno de **US$ 0,35 por dia (~US$ 10/mês)**; o gasto real costuma ser bem menor, porque a IA só é chamada quando há manchete nova ou mudança nos candidatos. Dá para trocar qualquer modelo pela tela (ela lista o catálogo do OpenRouter com preços) e restaurar os econômicos com um clique.

Detalhes técnicos: chamadas no formato `chat/completions` com `response_format` (JSON Schema estrito) e `provider.require_parameters`; se o modelo não aceitar JSON Schema, cai para o modo JSON simples com o schema no prompt; respostas sempre validadas com Pydantic; cache de prompt nos modelos da Anthropic.

### Claude direto (Anthropic)

Opcional. Com a chave da Anthropic (`MB_ANTHROPIC_API_KEY` ou pela tela), o provedor **Automático** passa a usar o Claude direto: Sonnet 5.5 para o gerente e Haiku 4.5 para notícias e auditoria (trocáveis na tela). Usa *structured outputs*, cache do prompt de sistema e o *fallback* automático no servidor. Você pode forçar o provedor em **Config. → Provedor**.

![Configuração da IA](docs/img/ia.png)

## MetaTrader 5: qualquer corretora

O MT5 **não roda na stack do servidor**. Ele fica onde funciona melhor: num PC ou VPS Windows, ou em Docker numa máquina Intel/AMD. Toda a inteligência continua no servidor: agentes, estratégias, risco, IA e decisões. Ao lado do MT5 roda só o **bridge do Meta-Bot** ([`mt5/Metatrader/bridge/metabot_bridge.py`](mt5/Metatrader/bridge/metabot_bridge.py)), um servidor HTTP pequeno, com **token obrigatório**. Ele expõe conta, símbolos, cotações, candles, posições, ordens e histórico, e envia, fecha e modifica ordens, escolhendo o tipo de preenchimento que a corretora aceita.

```
Meta-Bot (servidor)  ──HTTP + token, via Tailscale──►  bridge  ──►  MetaTrader 5  ──►  corretora
```

- **PC ou VPS Windows (recomendado):** MT5 da corretora com o **Algo Trading** ligado, Python 3.12 e o [`iniciar-bridge.bat`](mt5/windows/iniciar-bridge.bat). Passo a passo completo em [`mt5/windows/LEIA-ME.md`](mt5/windows/LEIA-ME.md).
- **Linux Intel/AMD com Docker:** [`deploy/mt5-remoto.yml`](deploy/mt5-remoto.yml) sobe o MT5 no Wine (base [gmag11/MetaTrader5-Docker](https://github.com/gmag11/MetaTrader5-Docker)) com o bridge incluso e painel web.
- **Ligação com o servidor:** [Tailscale](https://tailscale.com), uma rede privada grátis. A porta do bridge fica liberada só para a rede do Tailscale (pelo firewall do Windows ou publicando a porta no IP do Tailscale), e toda requisição precisa do token.
- **No Meta-Bot:** coloque `MB_MT5_BRIDGE_URL=http://IP-DO-TAILSCALE:8001` na stack, ou cadastre em **Config. → MetaTrader 5** e clique em *Testar*.
- **Qualquer corretora que ofereça MT5:** forex, índices, cripto e corretoras brasileiras com MT5 para B3. Para usar várias ao mesmo tempo, rode um MT5 e um bridge por conta e cadastre cada um. O terminal marcado como ativo é o usado.
- **Ordens de verdade** só depois de **Config. → Modo de operação → Usar a conta do MT5**. Antes disso, o sistema usa os preços da corretora e opera no simulado. O Meta-Bot só mexe nas posições com o *magic number* dele.

Enquanto o MT5 não está conectado, o sistema usa um **mercado simulado** (determinístico, com sessões, volatilidade por hora e regimes) para você ver a equipe trabalhando desde o primeiro minuto.

## Subir no Portainer (trade.tekvosoft.com)

A stack segue o mesmo padrão das suas outras stacks: rede externa `network_public`, Traefik com `websecure` e `letsencryptresolver`, `node.role == manager` e o **Postgres que você já tem (`postgres_postgres`)** — o banco `metabot` é criado sozinho.

**1. Imagens.** Todo push na `main` roda o GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): testes, teste de ponta a ponta e publicação de três imagens no GHCR:

- `ghcr.io/dhqdev/meta-bot-backend:latest`
- `ghcr.io/dhqdev/meta-bot-frontend:latest`
- `ghcr.io/dhqdev/meta-bot-mt5:latest`

Como o repositório é público, as imagens também são: o Portainer baixa sem precisar de login. (Se um dia o repositório virar privado, cadastre o registro no Portainer em *Registries → Add registry → Custom*: `ghcr.io`, usuário `dhqdev`, senha = token do GitHub com `read:packages`.)

**2. DNS.** Aponte `trade.tekvosoft.com` para o servidor (registro A). Se outra stack já usa esse domínio no Traefik (por exemplo, uma versão antiga do bot), remova-a ou troque o domínio dela para não haver conflito de rota.

**3. Stack.** No Portainer: *Stacks → Add stack*, nome `metabot`, cole o conteúdo de [`deploy/portainer-stack.yml`](deploy/portainer-stack.yml) e troque os valores `TROQUE_...`:

| Variável | O que colocar |
|---|---|
| `MB_SECRET_KEY` | 64 caracteres aleatórios (`openssl rand -hex 32`). Guarde: ela criptografa as chaves salvas. |
| `MB_DATABASE_URL` | a senha do seu Postgres no lugar de `TROQUE_SENHA_DO_POSTGRES` (caracteres especiais em formato de URL, ex.: `@` → `%40`). |
| `MB_ADMIN_EMAIL` / `MB_ADMIN_PASSWORD` | o seu login no Meta-Bot (criado na primeira inicialização). |
| `MB_MT5_BRIDGE_TOKEN` | valor aleatório (`openssl rand -hex 24`). O **mesmo** valor vai no `iniciar-bridge.bat` da máquina do MT5. |
| `MB_MT5_BRIDGE_URL` | endereço do bridge, ex.: `http://100.101.102.103:8001`. Pode ficar vazio: o sistema usa o mercado simulado até você ligar o MT5. |
| `MB_OPENROUTER_API_KEY` | a sua chave do OpenRouter (pode deixar vazio e cadastrar depois pela tela). |

O backend **se recusa a subir** se algum `TROQUE_...` ficar para trás. O bridge do MT5 também recusa o token de exemplo. Clique em *Deploy the stack*. A stack roda em servidores Intel/AMD e ARM.

**4. Primeiro acesso.** Abra `https://trade.tekvosoft.com`, entre com o e-mail e a senha que definiu, ative a verificação em duas etapas em **Config. → Segurança** e, se quiser, apague `MB_ADMIN_EMAIL`/`MB_ADMIN_PASSWORD` da stack. Clique em **Ligar escritório**. Quando quiser, ligue o MetaTrader 5 (seção anterior).

**5. Atualizações.** Depois de cada build: *Stacks → metabot → Update the stack → Re-pull image*. Para automatizar, crie webhooks dos serviços no Portainer e salve as URLs (separadas por espaço) no secret `PORTAINER_WEBHOOK_URL` do repositório: o job `deploy` chama todas no fim do CI.

Dados persistentes: volume `metabot_data` (backend) e o banco `metabot` no seu Postgres.

## Rodar na sua máquina

**Tudo com Docker Compose** (sobe Postgres próprio, sem Traefik):

```bash
cp .env.example .env        # preencha as senhas e, se quiser, a chave do OpenRouter
docker compose up -d --build
# http://localhost:8080  (máquina Intel/AMD: MT5 em Docker junto, painel em http://localhost:8080/mt5/)
```

**Desenvolvimento** (sem Docker, SQLite e mercado simulado):

```bash
# backend (Python 3.12)
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
MB_ADMIN_EMAIL=voce@exemplo.com MB_ADMIN_PASSWORD=UmaSenhaForte123 uvicorn app.main:app --reload

# frontend (Node 22), em outro terminal
cd frontend
npm install
npm run dev     # http://localhost:5173 (encaminha /api e /ws para o backend)
```

Sem `MB_ADMIN_*`, o backend mostra no log um **código de configuração** para criar a conta do dono pela tela.

## Segurança

- Login com sessão em cookie `HttpOnly`/`SameSite`, senha com scrypt e **verificação em duas etapas (TOTP)** opcional.
- Proteção contra CSRF (conferência de origem), limite de tamanho de requisição e cabeçalhos de segurança (CSP, `X-Frame-Options`, `nosniff`).
- Chaves de IA, token do bridge e senha da corretora ficam **criptografados** no banco (Fernet, derivado de `MB_SECRET_KEY`); a tela só mostra a versão mascarada.
- Ligar o modo **conta da corretora** exige senha, confirmação explícita e MT5 conectado; trocar chaves e terminais também pede a senha.
- *Kill switch* e limites de perda diária/drawdown da Rita; ao desligar o escritório, as posições abertas continuam protegidas pelo Caio.
- Bridge do MT5 com token e acessível só pela rede privada do Tailscale. No docker-compose local, o painel do MT5 fica atrás do login do Meta-Bot (`auth_request` no nginx) **e** da senha do painel.
- Conteúdo das notícias é tratado como dado externo no prompt (instruções dentro delas são ignoradas) e toda resposta da IA é validada antes de ser usada; a IA nunca envia ordens diretamente — passa sempre pelas regras de risco e caixa.

## Configurações

Quase tudo é ajustado pela tela **Config.** e vale na hora: ativos e tempos gráficos, estratégias habilitadas, critério do ranking, aprovação, risco por operação, limites, gestão de saída, fechamento da B3, bloqueio por calendário, fontes de notícias, provedor e modelos de IA, limites de gasto, senha e 2FA.

Variáveis de ambiente (prefixo `MB_`, backend):

| Variável | Padrão | Descrição |
|---|---|---|
| `MB_ENV` | `development` | `production` exige `MB_SECRET_KEY` forte. |
| `MB_SECRET_KEY` | — | chave de criptografia e sessão (32+ caracteres). |
| `MB_DATABASE_URL` | SQLite em `MB_DATA_DIR` | ex.: `postgresql+psycopg://usuario:senha@host:5432/metabot` (o banco é criado se não existir). |
| `MB_DATA_DIR` | `./data` (`/data` na imagem) | arquivos locais. |
| `MB_ADMIN_EMAIL` / `MB_ADMIN_PASSWORD` | — | cria o dono na primeira inicialização. |
| `MB_COOKIE_SECURE` | `false` | `true` atrás de HTTPS. |
| `MB_PUBLIC_URL` | — | endereço público (identifica o app no OpenRouter). |
| `MB_ALLOWED_ORIGINS` | — | origens extras aceitas (separadas por vírgula). |
| `MB_MT5_BRIDGE_URL` / `MB_MT5_BRIDGE_TOKEN` | — | terminal "MT5 principal" (segue a stack a cada inicialização; vazio = nenhum). |
| `MB_MT5_PANEL_URL` | — | link do painel web do MT5 na tela, se houver (ex.: `/mt5/` no docker-compose local). |
| `MB_OPENROUTER_API_KEY` | — | chave do OpenRouter. |
| `MB_ANTHROPIC_API_KEY` | — | chave da Anthropic (opcional). |
| `MB_TIMEZONE` | `America/Sao_Paulo` | fuso usado na tela e no diário. |

Variáveis do bridge e do container do MT5 (`MT5_BRIDGE_TOKEN`, `MT5_BRIDGE_PORT`, `MT5_TERMINAL_PATH`, `MT5_LOGIN`, `MT5_INSTALLER_URL`…) estão em [`mt5/README.md`](mt5/README.md) e [`mt5/windows/LEIA-ME.md`](mt5/windows/LEIA-ME.md).

## Estrutura do projeto e API

```
meta-bot/
├── backend/                 FastAPI + agentes (Python 3.12)
│   ├── app/agents/          Tito, Nina, Hugo, Estela, Gustavo, Rita, Caio, Aurora + playbooks
│   ├── app/core/            indicadores, 18 estratégias, backtest, métricas, evolução, risco
│   ├── app/broker/          cliente do bridge MT5, corretor de papel, mercado simulado
│   ├── app/services/        IA (OpenRouter/Anthropic), feeds de notícias e calendário
│   ├── app/api/             rotas REST + WebSocket
│   └── tests/               103 testes
├── frontend/                React + Vite + Tailwind; escritório em canvas (pixel-art gerada no código)
│   ├── src/office/          mapa, sprites, pathfinding (A*), cenário e motor de animação
│   ├── nginx/               proxy de /api, /ws e /mt5 (com auth_request)
│   └── e2e/smoke.mjs        teste de ponta a ponta (Playwright)
├── mt5/                     bridge HTTP do Meta-Bot, kit Windows (mt5/windows) e MT5 em Docker (base gmag11)
├── deploy/portainer-stack.yml   stack do Portainer (Swarm + Traefik)
├── deploy/mt5-remoto.yml    MT5 em Docker numa máquina Linux Intel/AMD, via Tailscale
├── docker-compose.yml       tudo local com Docker Compose
└── .github/workflows/ci.yml testes, e2e e imagens no GHCR
```

API (todas exigem login, exceto `/api/health` e `/api/auth/status|setup|login`):

| Grupo | Rotas |
|---|---|
| Sistema | `GET /api/health`, `GET /api/system`, `POST /api/system/running`, `POST /api/system/mode`, `POST /api/system/kill-switch/reset`, `POST /api/system/paper/reset` |
| Autenticação | `/api/auth/status`, `setup`, `login`, `logout`, `me`, `password`, `2fa/setup`, `2fa/enable`, `2fa/disable` |
| Agentes | `GET /api/agents`, `GET /api/agents/{id}`, `POST /api/agents/{id}/run`, `GET /api/activity`, `GET/DELETE /api/lessons` |
| Estratégias | `GET /api/strategies`, `GET /api/strategies/ranking`, `GET /api/strategies/profiles/{id}`, `POST /api/strategies/ranking/run`, `POST /api/strategies/evolution/run`, `POST /api/strategies/backtest`, `GET /api/strategies/signals` |
| Mercado | `GET /api/market/symbols`, `candles`, `overview`, `hours`, `GET /api/news`, `GET /api/calendar` |
| Operações | `GET /api/trades`, `GET /api/trades/open`, `POST /api/trades/{id}/close`, `GET /api/trades/summary`, `GET /api/decisions` |
| Configurações | `GET/PUT /api/settings`, `POST /api/settings/ai-key`, `GET /api/settings/openrouter-models`, terminais MT5 em `/api/settings/terminals` |
| Tempo real | `WS /ws` (estado do escritório, falas, reuniões, operações) |

## Testes e CI

```bash
cd backend && python -m pytest -q          # 103 testes
cd frontend && npm run build               # typecheck + build
BASE_URL=http://127.0.0.1:4173 node frontend/e2e/smoke.mjs   # com backend e "npm run preview" no ar
```

Os testes cobrem: estratégias sem olhar o futuro, backtest (custos, stops, ordens stop), métricas, evolução, risco e lote, bridge do MT5 (com um MT5 falso), API (login, 2FA, CSRF, modo real), agentes, feeds e a camada de IA (Anthropic e OpenRouter, com respostas simuladas). O CI roda tudo isso, o teste de ponta a ponta no navegador e publica as imagens.

## Créditos

- MetaTrader 5 em Docker: [gmag11/MetaTrader5-Docker](https://github.com/gmag11/MetaTrader5-Docker) (licença em [`mt5/LICENSE-gmag11.md`](mt5/LICENSE-gmag11.md)), sobre as imagens da [LinuxServer.io](https://www.linuxserver.io/).
- Estratégias inspiradas nos materiais de [Vilela One](https://vilela.one/) e [IndicatorSpot](https://indicatorspot.com/), em setups brasileiros populares e em estratégias clássicas. As implementações são próprias e foram adaptadas para backtest.
- MetaTrader é marca da MetaQuotes. Este projeto não tem relação com a MetaQuotes nem com as corretoras.
