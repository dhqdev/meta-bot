# Meta-Bot

**Um escritório de trading com oito agentes que trabalham juntos, conversam entre si, fazem uma daily no fim do dia e melhoram com o resultado real, operando no MetaTrader 5 de qualquer corretora.** Você acompanha tudo numa sala em pixel-art, vista de cima: cada agente na sua mesa, mensagens voando de um para o outro, reuniões na sala do gerente e comemoração quando a meta do dia é batida.

![Escritório do Meta-Bot](docs/img/escritorio.png)

- **Objetivo: terminar o dia no lucro.** Você define o **limite de perda do dia** e a **meta de ganho**; bateu um dos dois, a equipe para até amanhã.
- **Agentes com personalidade**: notícias, horários e calendário, estratégias, gerente, risco, caixa, auditoria e TI, cada um com seu jeito de falar. A **conversa da equipe** aparece ao vivo no escritório.
- **Daily às 19h**: a equipe se reúne, avalia o dia, escreve um relatório (aba **Daily**) e aplica os aprendizados no dia seguinte.
- **Scalper ou segurar mais tempo?** A equipe testa os dois estilos e passa a preferir o que dá mais resultado.
- **Estratégias testadas de verdade**: 18 setups (Vilela One, IndicatorSpot, setups brasileiros e clássicos), backtest com custos e validação fora da amostra.
- **IA econômica e sem configuração**: só a chave do OpenRouter; cada agente já tem o seu modelo definido. Sem chave, tudo funciona só com regras.
- **MetaTrader 5 de qualquer corretora**, num PC ou VPS Windows. Toda a inteligência fica no servidor.
- **Pronto para o Portainer** (Docker Swarm + Traefik) em `trade.tekvosoft.com`, com imagens publicadas pelo GitHub Actions (Intel/AMD e ARM).
- **App no celular (PWA)**: instale na tela inicial; todas as telas se adaptam.

> ⚠️ Trading envolve risco de perda. O sistema começa no **modo simulado**; teste em conta **demo** antes de ligar numa conta real.

---

## Sumário

1. [A equipe](#a-equipe)
2. [Como uma operação acontece](#como-uma-operação-acontece)
3. [Metas do dia](#metas-do-dia)
4. [Daily das 19h](#daily-das-19h)
5. [Scalper × day trade × posição longa](#scalper--day-trade--posição-longa)
6. [Skills que evoluem](#skills-que-evoluem)
7. [Estratégias e backtest](#estratégias-e-backtest)
8. [Inteligência artificial (OpenRouter)](#inteligência-artificial-openrouter)
9. [Configurações](#configurações)
10. [Celular e app (PWA)](#celular-e-app-pwa)
11. [MetaTrader 5: qualquer corretora](#metatrader-5-qualquer-corretora)
12. [Subir no Portainer (trade.tekvosoft.com)](#subir-no-portainer-tradetekvosoftcom)
13. [Rodar na sua máquina](#rodar-na-sua-máquina)
14. [Segurança](#segurança)
15. [Estrutura do projeto e API](#estrutura-do-projeto-e-api)
16. [Testes e CI](#testes-e-ci)
17. [Skill para o Claude Code](#skill-para-o-claude-code)
18. [Créditos](#créditos)

---

## A equipe

| Agente | Função | Jeito | IA? | O que faz e o que aprende |
|---|---|---|---|---|
| **Tito** | TI e Dados | o nerd tranquilo da infra | não | Mantém o MT5 conectado (qualquer corretora), estima o fuso do servidor, aquece o cache de candles e faz a manutenção do banco. |
| **Nina** | Notícias | a repórter curiosa | **sim** | Lê RSS de FXStreet, Investing, CNBC, MarketWatch, Yahoo, CoinDesk, InfoMoney, Money Times e Investing Brasil. Classifica impacto e sentimento por ativo, avisa o gerente das notícias fortes e depois confere se acertou a direção, dando mais peso às fontes que acertam. |
| **Hugo** | Horários e Calendário | o relógio da equipe | não | Mapeia os melhores horários de cada ativo, acompanha o calendário econômico (Forex Factory), avisa a equipe antes dos eventos fortes e pausa as entradas nos ativos afetados. |
| **Estela** | Estrategista | a cientista cética | não | Roda os backtests de todas as estratégias em cada ativo e tempo gráfico, aprova só o que se sustenta fora da amostra, evolui os parâmetros (inclusive versões scalper e de segurar mais tempo) e gera os sinais. |
| **Gustavo** | Gerente | o líder calmo | **sim** | Junta tudo (backtest, hora, notícias, calendário, resultado real, risco e o foco da daily) e decide **qual ativo, qual estratégia e em qual horário** a mesa opera. Aprova ou recusa cada sinal, conduz as reuniões e a daily e aprende quanto confiar em cada colega. |
| **Rita** | Risco | a guardiã do caixa | não | Calcula o lote pelo risco por operação, controla posições, exposição por moeda e spread, **encerra o dia na meta ou no limite de perda** e tem a trava geral. Fica mais conservadora depois de perdas. |
| **Caio** | Caixa | o executor disciplinado | não | Envia as ordens com **stop loss e stop gain**, faz zero a zero, trailing, saída por tempo, fecha antes do fim de semana e antes do fechamento da B3. |
| **Aurora** | Auditoria | a mentora sábia | **sim** (daily) | Compara o resultado real com o backtest, põe em observação o que decepciona, distribui XP e registra as lições da daily que a equipe passa a seguir. |

Cada um tem bio, traços e bordões; as falas nos balões, na conversa e na daily saem no jeito de cada agente (`backend/app/agents/personas.py`). A tela **Agentes** mostra a personalidade, as skills, o nível e o histórico; dá para mandar um agente executar uma tarefa na hora.

![Agentes](docs/img/agentes.png)

## Como uma operação acontece

```mermaid
flowchart LR
    N[Nina<br/>notícias] --> G
    H[Hugo<br/>horários e calendário] --> G
    E[Estela<br/>backtests e ranking] --> G
    D[Daily de ontem<br/>foco e ajustes] --> G
    G[Gustavo<br/>plano: ativo, estratégia, horário] --> S{Sinal da Estela<br/>num setup do plano}
    S --> G2[Gustavo revisa<br/>notícias e calendário]
    G2 --> R[Rita<br/>lote, limites e metas do dia]
    R --> C[Caio<br/>ordem, SL/TP, trailing]
    C --> A[Aurora<br/>auditoria e XP]
```

1. A cada 15 minutos o **Gustavo** monta o plano com até 3 setups (ativo + tempo gráfico + estratégia + direção). Com IA, ele escolhe entre candidatos que as regras já filtraram; sem IA, usa a pontuação da equipe.
2. Quando fecha um candle, a **Estela** confere os sinais dos setups do plano e manda para o Gustavo.
3. O **Gustavo** revisa o sinal contra notícias e calendário e pede o lote à **Rita**; ela confere os limites e passa o lote ao **Caio**, que executa com stop e alvo.
4. Quando a operação fecha, a **Aurora** atualiza as estatísticas reais, distribui XP e o Gustavo ajusta a confiança em cada colega.

Cada passo é uma **mensagem da equipe** (pedido → resposta), que aparece na aba **Conversa** do escritório e como um envelope voando de uma mesa para a outra. Avisos para todo mundo (evento chegando, meta batida, MT5 caiu) aparecem como uma onda saindo de quem falou.

No modo **simulado**, o Caio usa um corretor de papel com spread, slippage e comissão. No modo **conta da corretora**, as ordens vão para o MT5 (exige senha, confirmação e MT5 conectado).

## Metas do dia

O que importa é o resultado no fim do dia. Em **Config. → Metas do dia e risco**:

- **Limite de perda do dia** (padrão 3% do patrimônio): chegou nessa perda, a Rita **para a equipe até amanhã** — nada de tentar recuperar no mesmo dia.
- **Meta de ganho do dia** (padrão desligada): bateu a meta, a equipe encerra o dia no lucro.
- Os dois podem ser em **% do patrimônio** ou em **valor** (na moeda da conta).
- **Encerrar as posições abertas ao bater a meta ou o limite** (padrão ligado): o Caio fecha tudo na hora e o resultado fica garantido. Desligado, as abertas seguem até o stop ou o alvo, mas nenhuma nova entra.
- **Perfil rápido**: *Conservador*, *Moderado* ou *Arrojado* preenchem risco por operação, limite, meta e número de posições de uma vez.

O escritório mostra o progresso até a meta e até o limite; o topo da tela mostra o resultado do dia e, quando a equipe para, o motivo (🎯 meta batida ou ⛔ limite).

## Daily das 19h

![Daily](docs/img/daily.png)

Todo dia às **19h** (horário de Brasília, ajustável), se a equipe trabalhou, todos vão para a sala de reunião:

1. **Números do dia**: operações, resultado, acerto, estratégias, ativos, horários, estilo (scalper × day trade × posição longa), saídas, sinais vetados, notícias, eventos e metas.
2. **Cada agente avalia a sua área** e decide **ajustes para amanhã**, sempre com limite:
   - Hugo: horário que deu prejuízo hoje fica evitado amanhã (no ativo ou em todos);
   - Estela: estratégia que perdeu várias vezes no dia vai para revalidação;
   - Gustavo: recalibra a preferência entre scalper, day trade e posição longa;
   - Rita: depois de um dia no limite de perda, começa amanhã com menos risco (e recupera aos poucos com dias positivos);
   - Caio: muitas saídas por tempo → revisa a gestão de saída à noite.
3. **A reunião**: com IA, cada agente fala no seu jeito, com os números do dia; sem IA, as falas saem das próprias análises.
4. **Relatório** na aba **Daily**: resumo, falas, foco de amanhã, ajustes, lições e números. As lições entram no prompt dos agentes, e o Gustavo lembra a equipe do foco na manhã seguinte. Cada daily dá XP na skill *Aprendizado da daily*.

Dá para fazer a daily a qualquer hora pelo botão **Fazer a daily agora**.

## Scalper × day trade × posição longa

Quanto tempo a operação fica aberta faz diferença. A equipe classifica cada operação pelo tempo médio em posição — **scalper** (até 30 min), **day trade** (até 8 h) ou **posição longa** (mais de 8 h) — e compara os três no backtest fora da amostra e nas operações reais.

- A evolução da Estela sempre testa a **versão scalper** (stop de 1 ATR, alvo de 1R, sai em até 6 candles) e a de **segurar mais** (alvo de 3R, mais tempo) de cada estratégia.
- Na daily, o Gustavo ajusta o peso de cada estilo (entre 0,75 e 1,25, no máximo 30% do caminho por dia) e passa a preferir o que está dando resultado.
- A tela **Estratégias** mostra a comparação e o estilo preferido; o tempo gráfico **M5** (padrão ligado) dá espaço para o scalper.

![Estratégias](docs/img/estrategias.png)

## Skills que evoluem

- **XP e níveis**: cada skill sobe de nível (1 a 10) com trabalho e, principalmente, com resultado real — backtest aprovado, evolução confirmada fora da amostra, notícia que acertou a direção, operação bem gerida, daily concluída.
- **Evolução de estratégias** (Estela, a cada 24 h): variações de parâmetros, filtros, stop e alvo das melhores estratégias, testadas com *walk-forward* (70% para escolher, 30% fora da amostra). Só entra a nova versão (`v2`, `v3`…) se melhorar sem perder a expectativa.
- **Revalidação**: estratégia que vai mal no real entra "em observação" e é retestada antes de voltar ao plano.
- **Gestão de saída adaptativa** (Caio): aprende zero a zero e trailing que funcionam.
- **Risco adaptativo** (Rita): reduz depois de perdas seguidas e volta aos poucos.
- **Confiança na equipe** (Gustavo): pesos para Estela, Hugo, Nina e resultado real, ajustados a cada operação.
- **Daily**: ajustes para o dia seguinte, preferência de estilo e lições que entram no prompt dos agentes com IA, junto com o manual da equipe (`backend/app/agents/playbooks/equipe.md`) e o playbook da função.

## Estratégias e backtest

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
- stop e alvo por ATR, zero a zero, trailing e saída por tempo iguais aos do Caio;
- métricas: taxa de acerto, **acerto mínimo (limite inferior de Wilson, 95%)**, fator de lucro, expectativa em R, queda máxima, tempo médio em posição, resultado por hora;
- **aprovação**: mínimo de 25 operações, fator de lucro ≥ 1,1 e desempenho positivo nos 30% finais que não foram usados para escolher.

Na tela **Estratégias** há o ranking (com o estilo de cada setup), o backtest sob demanda com gráfico e o catálogo com a explicação de cada estratégia.

## Inteligência artificial (OpenRouter)

A IA é opcional: sem chave, todos os agentes funcionam só com as regras. Coloque a chave do [OpenRouter](https://openrouter.ai/keys) na stack (`MB_OPENROUTER_API_KEY`) ou em **Config. → Inteligência artificial**. Não há modelo para escolher: **cada agente já tem o seu**, definido no sistema pensando em economia, porque a equipe fica ligada o dia todo.

| Tarefa | Modelo | Por quê | Preço aprox. (US$ por milhão de tokens, entrada/saída) |
|---|---|---|---|
| Nina · notícias | `deepseek/deepseek-v4-flash` | é quem mais chama a IA; classificação simples | 0,09 / 0,18 |
| Gustavo · plano | `google/gemini-3.1-flash-lite` | escolhe entre candidatos já filtrados pelas regras | 0,25 / 1,50 |
| Daily (Aurora escreve) | `anthropic/claude-haiku-4.5` | uma vez por dia: as falas de cada agente, o resumo e as lições | 1,00 / 5,00 |
| Reserva | `google/gemini-3.1-flash-lite` | se um modelo falhar ou sair do ar | 0,25 / 1,50 |

Economias ligadas por padrão:
- a Nina junta as manchetes novas e chama a IA no máximo **a cada 15 min**;
- o Gustavo **reaproveita o plano da IA** enquanto os candidatos não mudam (validade de 60 min);
- **limite de 12 chamadas por hora** e **orçamento de US$ 0,50 por dia** (passou disso, os agentes seguem só com as regras até virar o dia);
- o custo real de cada chamada vem do OpenRouter e aparece na tela (hoje, 30 dias e por agente).

No pior caso (IA chamada em todos os intervalos), o gasto fica perto de **US$ 0,15 por dia (~US$ 5 por mês)**; na prática é menor, porque a IA só é chamada quando há manchete nova ou mudança nos candidatos.

Detalhes técnicos: `chat/completions` com `response_format` (JSON Schema estrito) e `provider.require_parameters`; se o modelo não aceitar JSON Schema, cai para o modo JSON simples com o schema no prompt; respostas sempre validadas com Pydantic; cache de prompt nos modelos da Anthropic; a IA nunca envia ordens.

## Configurações

![Configurações](docs/img/config.png)

A tela **Config.** foi pensada para ser simples, porque os próprios agentes se ajustam:

- **No topo, o essencial:** modo de operação, metas do dia e risco (com perfis prontos), ativos e tempos gráficos, daily, chave da IA e MetaTrader 5.
- **Ajustes finos (opcional):** uma seção por agente (Rita, Estela, Gustavo, Caio, Hugo, Nina e conta simulada/MT5), fechada por padrão. Cada campo explica o que faz na prática, mostra o valor recomendado e volta para ele com um toque.
- Tudo vale na hora, sem reiniciar. Valores fora do permitido são recusados com uma mensagem clara (ex.: "Risco por operação: precisa ser no máximo 5").
- Os efeitos dos principais ajustes são verificados por testes automáticos (`backend/tests/test_settings_effects.py`).

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
| `MB_TIMEZONE` | `America/Sao_Paulo` | fuso usado na tela, nas metas do dia e na daily. |

Variáveis do bridge e do container do MT5 (`MT5_BRIDGE_TOKEN`, `MT5_BRIDGE_PORT`, `MT5_TERMINAL_PATH`, `MT5_LOGIN`, `MT5_INSTALLER_URL`…) estão em [`mt5/README.md`](mt5/README.md) e [`mt5/windows/LEIA-ME.md`](mt5/windows/LEIA-ME.md).

## Celular e app (PWA)

<img src="docs/img/celular.png" alt="Meta-Bot no celular" width="300" align="right">

O Meta-Bot é um **app instalável** (PWA):

- **Android (Chrome):** abra `https://trade.tekvosoft.com` e toque em **Instalar app** (no topo da tela) ou em *⋮ → Instalar app*.
- **iPhone (Safari):** toque em *Compartilhar → Adicionar à Tela de Início*.

Abre em tela cheia, com ícone próprio, respeita o entalhe e a barra de gestos, e abre na hora mesmo com a rede instável (os dados continuam sempre ao vivo: API e WebSocket nunca passam pelo cache). No celular, o menu fica embaixo, a faixa da equipe e os chips rolam para o lado, e o escritório tem botão de tela cheia.

<br clear="right">

## MetaTrader 5: qualquer corretora

O MT5 **não roda na stack do servidor**. Ele fica onde funciona melhor: num PC ou VPS Windows, ou em Docker numa máquina Intel/AMD. Toda a inteligência continua no servidor: agentes, estratégias, risco, IA e decisões. Ao lado do MT5 roda só o **bridge do Meta-Bot** ([`mt5/Metatrader/bridge/metabot_bridge.py`](mt5/Metatrader/bridge/metabot_bridge.py)), um servidor HTTP pequeno, com **token obrigatório**. Ele expõe conta, símbolos, cotações, candles, posições, ordens e histórico, e envia, fecha e modifica ordens, escolhendo o tipo de preenchimento que a corretora aceita.

```
Meta-Bot (servidor)  ──HTTP + token, via Tailscale──►  bridge  ──►  MetaTrader 5  ──►  corretora
```

- **PC ou VPS Windows (recomendado):** MT5 da corretora com o **Algo Trading** ligado, Python 3.12 e o [`iniciar-bridge.bat`](mt5/windows/iniciar-bridge.bat). Passo a passo completo em [`mt5/windows/LEIA-ME.md`](mt5/windows/LEIA-ME.md).
- **Linux Intel/AMD com Docker:** [`deploy/mt5-remoto.yml`](deploy/mt5-remoto.yml) sobe o MT5 no Wine (base [gmag11/MetaTrader5-Docker](https://github.com/gmag11/MetaTrader5-Docker)) com o bridge incluso e painel web.
- **Ligação com o servidor:** [Tailscale](https://tailscale.com), uma rede privada grátis. A porta do bridge fica liberada só para a rede do Tailscale, e toda requisição precisa do token.
- **No Meta-Bot:** coloque `MB_MT5_BRIDGE_URL=http://IP-DO-TAILSCALE:8001` na stack, ou cadastre em **Config. → MetaTrader 5** e clique em *Testar*.
- **Qualquer corretora que ofereça MT5:** forex, índices, cripto e corretoras brasileiras com MT5 para B3. Para usar várias, rode um MT5 e um bridge por conta e cadastre cada um. O terminal marcado como ativo é o usado.
- **Ordens de verdade** só depois de **Config. → Modo de operação → Usar a conta do MT5**. Antes disso, o sistema usa os preços da corretora e opera no simulado. O Meta-Bot só mexe nas posições com o *magic number* dele.

Enquanto o MT5 não está conectado, o sistema usa um **mercado simulado** (determinístico, com sessões, volatilidade por hora e regimes) para você ver a equipe trabalhando desde o primeiro minuto.

## Subir no Portainer (trade.tekvosoft.com)

A stack segue o mesmo padrão das suas outras stacks: rede externa `network_public`, Traefik com `websecure` e `letsencryptresolver`, `node.role == manager` e o **Postgres que você já tem (`postgres_postgres`)** — o banco `metabot` é criado sozinho.

**1. Imagens.** Todo push na `main` roda o GitHub Actions ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): testes, teste de ponta a ponta e publicação das imagens no GHCR:

- `ghcr.io/dhqdev/meta-bot-backend:latest` (Intel/AMD e ARM)
- `ghcr.io/dhqdev/meta-bot-frontend:latest` (Intel/AMD e ARM)
- `ghcr.io/dhqdev/meta-bot-mt5:latest` (só Intel/AMD; usada fora da stack, em [`deploy/mt5-remoto.yml`](deploy/mt5-remoto.yml))

Como o repositório é público, as imagens também são: o Portainer baixa sem precisar de login.

**2. DNS.** Aponte `trade.tekvosoft.com` para o servidor (registro A). Se outra stack já usa esse domínio no Traefik, remova-a ou troque o domínio dela para não haver conflito de rota.

**3. Stack.** No Portainer: *Stacks → Add stack*, cole o conteúdo de [`deploy/portainer-stack.yml`](deploy/portainer-stack.yml) e troque os valores `TROQUE_...`:

| Variável | O que colocar |
|---|---|
| `MB_SECRET_KEY` | 64 caracteres aleatórios (`openssl rand -hex 32`). Guarde: ela criptografa as chaves salvas. |
| `MB_DATABASE_URL` | a senha do seu Postgres no lugar de `TROQUE_SENHA_DO_POSTGRES` (caracteres especiais em formato de URL, ex.: `@` → `%40`). |
| `MB_ADMIN_EMAIL` / `MB_ADMIN_PASSWORD` | o seu login no Meta-Bot (criado na primeira inicialização). |
| `MB_MT5_BRIDGE_TOKEN` | valor aleatório (`openssl rand -hex 24`). O **mesmo** valor vai no `iniciar-bridge.bat` da máquina do MT5. |
| `MB_MT5_BRIDGE_URL` | endereço do bridge, ex.: `http://100.101.102.103:8001`. Pode ficar vazio: o sistema usa o mercado simulado até você ligar o MT5. |
| `MB_OPENROUTER_API_KEY` | a sua chave do OpenRouter (pode deixar vazio e cadastrar depois pela tela). |

O backend **se recusa a subir** se algum `TROQUE_...` ficar para trás. O bridge do MT5 também recusa o token de exemplo. Clique em *Deploy the stack*.

**4. Primeiro acesso.** Abra `https://trade.tekvosoft.com`, entre com o e-mail e a senha que definiu, ative a verificação em duas etapas em **Config. → Segurança** e, se quiser, apague `MB_ADMIN_EMAIL`/`MB_ADMIN_PASSWORD` da stack. Escolha um perfil em **Config. → Metas do dia e risco** e clique em **Ligar escritório**. Quando quiser, ligue o MetaTrader 5 (seção anterior).

**5. Atualizações.** Depois de cada build: *Stacks → (sua stack) → Update the stack → Re-pull image*. Para automatizar, crie webhooks dos serviços no Portainer e salve as URLs (separadas por espaço) no secret `PORTAINER_WEBHOOK_URL` do repositório: o job `deploy` chama todas no fim do CI. As tabelas novas (conversa da equipe, relatórios da daily) são criadas sozinhas na inicialização.

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
- Chave da IA, token do bridge e senha da corretora ficam **criptografados** no banco (Fernet, derivado de `MB_SECRET_KEY`); a tela só mostra a versão mascarada.
- Ligar o modo **conta da corretora** exige senha, confirmação explícita e MT5 conectado; trocar a chave e terminais também pede a senha.
- Metas do dia, trava geral e limites da Rita; ajustes automáticos **nunca aumentam o risco**; ao desligar o escritório, as posições abertas continuam protegidas pelo Caio.
- Bridge do MT5 com token e acessível só pela rede privada do Tailscale.
- Conteúdo das notícias é tratado como dado externo no prompt (instruções dentro delas são ignoradas) e toda resposta da IA é validada antes de ser usada; a IA nunca envia ordens diretamente — passa sempre pelas regras de risco e caixa.

## Estrutura do projeto e API

```
meta-bot/
├── backend/                 FastAPI + agentes (Python 3.12)
│   ├── app/agents/          Tito, Nina, Hugo, Estela, Gustavo, Rita, Caio, Aurora, personalidades,
│   │                        daily (daily.py) e playbooks (manual da equipe e de cada função)
│   ├── app/core/            indicadores, 18 estratégias, backtest, métricas, evolução, horizontes, risco
│   ├── app/broker/          cliente do bridge MT5, corretor de papel, mercado simulado
│   ├── app/services/        IA (OpenRouter), feeds de notícias e calendário
│   ├── app/api/             rotas REST + WebSocket
│   └── tests/               127 testes
├── frontend/                React + Vite + Tailwind; escritório em canvas (pixel-art gerada no código)
│   ├── src/office/          mapa, sprites, pathfinding (A*), motor de animação (mundo nítido + textos na resolução da tela)
│   ├── public/              manifesto, service worker e ícones do app (PWA)
│   ├── nginx/               proxy de /api e /ws, cabeçalhos e cache
│   └── e2e/smoke.mjs        teste de ponta a ponta (Playwright, desktop e celular)
├── mt5/                     bridge HTTP do Meta-Bot, kit Windows (mt5/windows) e MT5 em Docker (base gmag11)
├── deploy/portainer-stack.yml   stack do Portainer (Swarm + Traefik)
├── deploy/mt5-remoto.yml    MT5 em Docker numa máquina Linux Intel/AMD, via Tailscale
├── docker-compose.yml       tudo local com Docker Compose
├── .claude/skills/meta-bot/ skill do sistema para o Claude Code
└── .github/workflows/ci.yml testes, e2e e imagens no GHCR
```

API (todas exigem login, exceto `/api/health` e `/api/auth/status|setup|login`):

| Grupo | Rotas |
|---|---|
| Sistema | `GET /api/health`, `GET /api/system`, `POST /api/system/running`, `POST /api/system/mode`, `POST /api/system/kill-switch/reset`, `POST /api/system/paper/reset` |
| Autenticação | `/api/auth/status`, `setup`, `login`, `logout`, `me`, `password`, `2fa/setup`, `2fa/enable`, `2fa/disable` |
| Agentes | `GET /api/agents`, `GET /api/agents/{id}`, `POST /api/agents/{id}/run`, `GET /api/activity`, `GET /api/messages` (conversa da equipe), `GET/DELETE /api/lessons` |
| Daily | `GET /api/daily`, `GET /api/daily/{dia}`, `POST /api/daily/run` |
| Estratégias | `GET /api/strategies`, `GET /api/strategies/ranking`, `GET /api/strategies/horizons`, `GET /api/strategies/profiles/{id}`, `POST /api/strategies/ranking/run`, `POST /api/strategies/evolution/run`, `POST /api/strategies/backtest`, `GET /api/strategies/signals` |
| Mercado | `GET /api/market/symbols`, `candles`, `overview`, `hours`, `GET /api/news`, `GET /api/calendar` |
| Operações | `GET /api/trades`, `GET /api/trades/open`, `POST /api/trades/{id}/close`, `GET /api/trades/summary`, `GET /api/decisions` |
| Configurações | `GET/PUT /api/settings`, `POST /api/settings/ai-key`, terminais MT5 em `/api/settings/terminals` |
| Tempo real | `WS /ws` (estado do escritório, conversa da equipe, falas, reuniões, daily, operações) |

## Testes e CI

```bash
cd backend && python -m pytest -q          # 127 testes
cd frontend && npm run build               # typecheck + build
BASE_URL=http://127.0.0.1:4173 node frontend/e2e/smoke.mjs   # com backend e "npm run preview" no ar
```

Os testes cobrem: estratégias sem olhar o futuro, backtest (custos, stops, ordens stop), métricas e tempo em posição, evolução (variantes scalper e de segurar mais), risco e lote, **metas do dia** (limite, meta, fechamento e virada do dia), **daily** (relatório, ajustes, lições, foco da manhã, com e sem IA), preferência de estilo, **efeito de cada configuração**, bridge do MT5 (com um MT5 falso), API (login, 2FA, CSRF, modo real, mensagens de erro), agentes, feeds e a camada de IA (OpenRouter com respostas simuladas). O teste de ponta a ponta abre todas as telas, faz uma daily, confere a conversa, o PWA e o layout no celular. O CI roda tudo isso e publica as imagens.

## Skill para o Claude Code

A pasta [`.claude/skills/meta-bot/`](.claude/skills/meta-bot/SKILL.md) ensina o Claude Code como o sistema funciona: para que serve cada agente, o fluxo das operações, as regras que não se quebram, a daily, os comandos, as armadilhas conhecidas, o mapa de arquivos ([referencia.md](.claude/skills/meta-bot/referencia.md)) e o passo a passo das mudanças mais comuns ([receitas.md](.claude/skills/meta-bot/receitas.md)). Ao abrir o repositório no Claude Code, ela é carregada sozinha quando o assunto é o Meta-Bot.

Os próprios agentes também têm o seu manual: [`backend/app/agents/playbooks/equipe.md`](backend/app/agents/playbooks/equipe.md) (quem faz o quê, as regras e os estilos de operação), que vai no prompt da IA do Gustavo e da daily junto com o playbook da função, as personalidades e as lições em vigor.

## Créditos

- MetaTrader 5 em Docker: [gmag11/MetaTrader5-Docker](https://github.com/gmag11/MetaTrader5-Docker) (licença em [`mt5/LICENSE-gmag11.md`](mt5/LICENSE-gmag11.md)), sobre as imagens da [LinuxServer.io](https://www.linuxserver.io/).
- Estratégias inspiradas nos materiais de [Vilela One](https://vilela.one/) e [IndicatorSpot](https://indicatorspot.com/), em setups brasileiros populares e em estratégias clássicas. As implementações são próprias e foram adaptadas para backtest.
- MetaTrader é marca da MetaQuotes. Este projeto não tem relação com a MetaQuotes nem com as corretoras.
