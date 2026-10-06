---
name: meta-bot
description: Guia do sistema Meta-Bot (repositório dhqdev/meta-bot), uma equipe de 8 agentes de trading com IA (OpenRouter), escritório em pixel-art, daily das 19h, metas do dia e MetaTrader 5 remoto. Use sempre que for mexer, explicar, depurar, testar ou publicar qualquer parte do Meta-Bot (backend FastAPI, agentes, estratégias, frontend React, stack do Portainer, bridge do MT5).
---

# Meta-Bot: como o sistema funciona

Mesa de trading automatizada: **8 agentes** trabalham juntos, conversam entre si, fazem uma **daily às 19h** e melhoram a cada dia. O objetivo é **terminar o dia no lucro** com disciplina. Tudo em **português do Brasil**: código, comentários, textos da tela, commits e testes.

Leia [referencia.md](referencia.md) para o mapa de arquivos, rotas, eventos e chaves. Siga [receitas.md](receitas.md) para mudanças comuns (estratégia nova, campo de configuração, fala de agente, tela nova, deploy).

## A equipe (id → nome → função)

| id | Nome | Função | IA |
|---|---|---|---|
| `infra` | Tito | MT5/bridge, candles, fuso do servidor, limpeza do banco | não |
| `news` | Nina | notícias RSS, sentimento por ativo, confere se acertou | sim (`deepseek/deepseek-v4-flash`) |
| `schedule` | Hugo | qualidade de cada hora, calendário econômico, pausas por evento | não |
| `strategist` | Estela | backtests, ranking, aprovação fora da amostra, evolução, sinais | não |
| `manager` | Gustavo | plano (ativo + tempo gráfico + estratégia + direção), revisa sinais, conduz reuniões e a daily | sim (`google/gemini-3.1-flash-lite`) |
| `risk` | Rita | lote pelo risco, limites, **metas do dia**, trava geral | não |
| `cashier` | Caio | ordens com stop/alvo, zero a zero, trailing, saída por tempo, fechamentos | não |
| `auditor` | Aurora | real × backtest, observação, XP, lições; a IA da daily roda em nome dela (`anthropic/claude-haiku-4.5`) | sim |

Os modelos são **fixos no código** (`backend/app/services/llm.py`, `AGENT_MODELS`): o dono não escolhe modelo, só coloca a chave do OpenRouter. Reserva: `google/gemini-3.1-flash-lite`. Sem chave (ou IA desligada), **tudo funciona só com regras**.

## Fluxo de uma operação

1. Gustavo monta o plano a cada `decision_interval_minutes` (`manager.build_candidates` → `deterministic_plan` ou `ai_plan`). Candidatos bloqueados: evento (Hugo), hora fraca (`min_hour_quality`), hora evitada pela daily (`team.avoid_hours`, por ativo ou `"*"`).
2. Estela vê o sinal num setup do plano → `office.submit_signal` (pipeline com lock).
3. `manager.review_signal` (plano, direção, evento, notícias fortes) → `risk.evaluate` (meta do dia, trava, posições, exposição, spread, lote) → `cashier` executa.
4. Cada passo vira **mensagem da equipe** (`agent.tell(to, text, kind)`): pedido → resposta, visível na aba Conversa e como envelope voando no escritório.
5. Posição aberta: o Caio cuida do básico a cada 5 s (`cashier._manage`: stop/alvo no simulado, zero a zero, trailing, tempo, fim de semana, B3). De hora em hora (`position_review_minutes`) o Gustavo roda `manager.review_positions()`: monta o `PositionContext` e as regras de `app/agents/review.py` decidem fechar (`exit_reason="revisao"`), apertar o stop (nunca afrouxa; `cashier.adjust` recusa) ou mudar o alvo. Tudo passa pelo Caio com trava por posição (`cashier.trade_lock`). 12 candles depois de um fechamento pela revisão, `check_review_outcomes()` confere o contrafactual e ajusta `manager.review_patience` (−0,3 a +0,3).
6. Na saída, Aurora audita, a skill ganha XP e o Gustavo ajusta a confiança em cada colega.

## Regras que não se quebram

- **Risco nunca aumenta sozinho.** Ajustes automáticos (daily, adaptativo) só reduzem ou recuperam devagar até o normal. Pesos de horizonte ficam entre 0,75 e 1,25 e andam no máximo 30% por dia.
- **Meta do dia ou limite de perda batido = equipe parada até amanhã** (`risk._stop_day` → cancela pendentes, fecha posições se `close_on_daily_limit`, `manager.end_day`).
- **A IA nunca envia ordem.** Ela só escolhe entre candidatos já filtrados; toda resposta é validada com Pydantic (`complete_json` + `schema_model`) e passa pelas regras de risco e caixa.
- **Backtest honesto:** entrada na abertura do próximo candle, custos (spread, slippage, comissão), stop antes do alvo no mesmo candle, aprovação fora da amostra.
- **Segredos nunca no repositório** (o repo é público). Senhas e chaves vão na stack do Portainer ou criptografadas no banco.
- Modo conta real exige senha + confirmação + MT5 conectado.

## Daily (19h, horário de Brasília)

`backend/app/agents/daily.py` → `DailyMeeting`. `office.daily_loop` checa `due()` a cada 30 s (uma vez por dia, se a equipe trabalhou). Passos: `collect()` (números do dia) → `analyze()` (cada agente avalia sua área e decide **ajustes para amanhã** por regra) → `deterministic()` ou `with_ai()` (falas no jeito de cada um, resumo, lições, foco) → salva `DailyReport`, lições (`source="daily"`), XP na skill `aprendizado_daily`, reunião no escritório e evento `daily`. Na manhã seguinte `manager.morning_focus()` lembra o foco.

**Prévia × oficial:** o botão (`run(force=True)`) antes do `daily_meeting_time` é só **prévia** (`metrics.preview`): não grava `daily.last`, não aplica ajustes, lições nem XP. Os ajustes valem **uma vez por dia** (kv `daily.applied`); repetir a daily oficial só mostra os ajustes já decididos. Testes que dependem de aplicar usam `daily_meeting_time: "00:00"`. A IA da daily recebe `playbooks/daily.md` + `playbooks/equipe.md` + personalidades + lições.

O que muda no dia seguinte (provado em `tests/test_next_day.py`): hora evitada bloqueia candidato; estratégia em `observacao` com `live.revalidate_after` (daily: até amanhã 23:59; Aurora: +24 h) fica fora do plano e `strategist.revalidate_flagged` só revalida depois disso, exigindo backtest aprovado **e** as `RECENT_TRADES` operações mais recentes do teste no positivo (reprovada → `live.blocked_until` +3 dias, respeitado pelo ranking); `adaptive_mult` menor = lote menor; **todas** as lições ativas entram no prompt do plano da IA do Gustavo (`active_lessons(None)`), o cache do plano da IA é descartado depois da daily; foco lembrado de manhã.

**Fim de semana:** com `weekend_close` ligado, `office.check_weekend()` (no `daily_loop`) fecha o escritório quando o mercado de todos os ativos da `watchlist` está fechado (câmbio: sexta 18h a domingo 19h de Brasília) usando a mesma pausa (`office.break` com `kind="weekend"`) até `market_hours.next_open`. Desligado pelo dono = não mexe; religar na mão no fim de semana grava `office.weekend_skip` e vale até a reabertura. Na sexta a daily acontece mesmo com o escritório fechado.

**Pausa depois da daily:** só na daily automática (`run(force=False)`) e com o sistema ligado, `office.start_break(daily_break_minutes)` desliga `system_running`, cancela ordens stop armadas, guarda `office.break` no kv e publica `office_break`; `daily_loop` chama `check_break()` a cada 30 s e reabre sozinho. `POST /api/system/running` encerra a pausa sem reabrir (vale a escolha do dono).

## Personalidades e conversa

`backend/app/agents/personas.py`: `PERSONAS` (bio, traços, jeito de falar, bordões) e `LINES[agente][evento]` (falas com `{campos}`). Use `self.line("evento", campo=...)` para falar no jeito do agente e `self.tell(destino, texto, kind)` para mandar mensagem (`destino` = id do agente ou `"all"`; `kind` ∈ `info`, `pedido`, `resposta`, `alerta`, `comemoracao`, `daily`). As mensagens ficam em `agent_messages` (60 dias) e chegam ao front pelo WebSocket (`type: "message"`).

## Scalper × day trade × posição longa

`backend/app/core/horizons.py`: scalper ≤ 30 min, day trade ≤ 8 h, posição longa > 8 h. A evolução sempre testa a variante scalper (stop 1 ATR, alvo 1R, até 6 candles) e a de segurar mais (alvo 3R, mais tempo). `strategist.horizon_summary()` compara backtest e real; `manager.learn_horizons()` ajusta a preferência na daily; `build_candidates` multiplica a pontuação pelo peso.

## Origem dos preços

`MarketService.source()` (`backend/app/broker/market.py`): `mt5` (MT5 conectado) > `real` (preços públicos: `backend/app/broker/realdata.py`, Yahoo Finance para forex/índices/B3 e Binance para cripto e ouro via PAXG) > `synthetic` (sem internet: testes e `MB_NETWORK_ENABLED=false`). A conta simulada opera com qualquer uma. `office.sync_data_family()` detecta a troca entre preço simulado e real e reinicia o aprendizado (arquiva trades com `mode="paper-sim"`, reseta perfis, lições, pesos e a conta simulada). Alguns tickers do Yahoo têm atraso (`Route.delay`); a Estela tolera esse atraso ao conferir candles fechados.

## Comandos

```bash
# backend (venv com requirements-dev.txt)
cd backend && python -m pytest -q                      # ~155 testes, SQLite temporário, sem rede
cd backend && python scripts/verificar_sistema.py --minutos 5   # escritório com preços reais (precisa de internet)
cd backend && python scripts/diagnosticar_entradas.py --dias 10   # "por que não entrou?": refaz dias passados com preços reais e compara o plano antigo com o atual
MB_ADMIN_EMAIL=voce@exemplo.com MB_ADMIN_PASSWORD=UmaSenhaForte123 uvicorn app.main:app --reload

# frontend (Node 22)
cd frontend && npm run typecheck && npm run build
npm run dev                                            # http://localhost:5173 (proxy de /api e /ws)

# ponta a ponta (backend + "npx vite preview --port 4173" no ar)
BASE_URL=http://127.0.0.1:4173 E2E_EMAIL=... E2E_PASSWORD=... node frontend/e2e/smoke.mjs
```

Antes de dar algo por pronto: testes do backend, `npm run build` (typecheck) e, se mexeu em tela, o e2e. O CI (`.github/workflows/ci.yml`) roda tudo isso e publica as imagens no GHCR a cada push na `main`.

## Armadilhas conhecidas

- `Base.metadata.create_all` cria **tabelas novas**, mas **não adiciona colunas** em tabelas que já existem no Postgres de produção. Prefira tabela nova ou guardar em JSON/kv; coluna nova exige migração manual.
- Configuração editável mora no kv (`runtime_config`) e é validada por `RuntimeConfig` (`backend/app/runtime.py`). Mudou o formato? Suba `CONFIG_VERSION` e converta em `_migrate`. A tela de Configurações **salva sozinha** (~1 s depois da mudança e ao sair da tela); campo recusado aparece no rodapé e não é reenviado até mudar.
- "Por que não operou?": `GET /api/system/diagnostico` (painel *Mercado e operações hoje* no escritório) junta horário do câmbio (`core/market_hours.py`, fecha sexta 18h e abre domingo 19h de Brasília), estratégias aprovadas, candidatos bloqueados, plano, sinais e vetos do dia.
- Horários: o banco guarda UTC; a tela e a daily usam `MB_TIMEZONE` (America/Sao_Paulo). Comparações de validade (ex.: `team.avoid_hours[...]["until"]`) são strings ISO em UTC.
- Preços reais dependem de APIs públicas sem garantia (Yahoo pode responder 429). `RealMarket` faz cache incremental, recua 1 min no 429 e usa o último histórico; nunca misture preço simulado com real numa mesma série. O ambiente de nuvem do Claude Code pode bloquear Yahoo/Binance: a prova ao vivo é o workflow **Verificação com preços reais** no GitHub Actions.
- **cTrader (alternativa ao MT5, sem Windows):** `backend/app/broker/ctrader.py` fala com a cTrader Open API (protobuf sobre TCP/SSL, `live|demo.ctraderapi.com:5035`; mensagens geradas em `app/broker/ctrader_proto/`) e devolve tudo no formato do `MT5Client`. O terminal fica com `bridge_url = "ctrader://live|demo"`, `login` = ctidTraderAccountId e as credenciais (client id/secret + tokens) em JSON criptografado no `token_enc` (sem coluna nova). Conexão pela tela: `app/api/ctrader.py` (OAuth do cTrader ID → escolher a conta). Testes com servidor falso: `tests/fake_ctrader.py` + `MB_CTRADER_ENDPOINT`.
- O servidor de produção é **ARM**: imagens multi-arch (backend e frontend). O MT5 **não roda na stack**; fica num PC/VPS Windows com o bridge, ligado por Tailscale (`MB_MT5_BRIDGE_URL` + `MB_MT5_BRIDGE_TOKEN`).
- Testes de agentes usam `BTCUSD` (mercado simulado aberto 24h). Para matar processos em testes locais, ache o PID com `ps` e use `kill <pid>` (padrões do `pkill` podem casar com o próprio shell).
- Textos de tela, falas e mensagens de erro da API são para um dono leigo: português simples, sem jargão desnecessário (ver `FIELD_LABELS` em `backend/app/api/settings.py`).
