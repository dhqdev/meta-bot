# Referência do Meta-Bot

## Mapa de arquivos

```
backend/app/
├── main.py              FastAPI: rotas, startup (cria tabelas, dono, sobe o escritório)
├── config.py            variáveis de ambiente (prefixo MB_) via pydantic-settings
├── runtime.py           RuntimeConfig: tudo que a tela de Config. edita (kv "runtime_config"), _migrate
├── models.py            tabelas SQLAlchemy 2.x (Postgres em produção, SQLite nos testes)
├── db.py / kv.py        sessão (session_scope), chave-valor e segredos criptografados
├── events.py            bus (eventos para o WebSocket) e record_activity (log de atividade)
├── security.py          senhas (scrypt), Fernet, máscara de chaves
├── agents/
│   ├── base.py          Agent: tick(), due(job, s), request(job), set_state/work/idle, say/tell/line, greet
│   ├── office.py        Office: cria os agentes, pipeline do sinal, publish_office, meeting, daily_loop
│   ├── personas.py      PERSONAS, LINES, speak(), persona_prompt(), persona_dict()
│   ├── skills.py        SkillBook (XP/níveis), lições (add_lesson, active_lessons), playbook()
│   ├── daily.py         DailyMeeting (daily das 19h), report_dict, DAILY_SKILL
│   ├── infra.py news.py schedule.py strategist.py manager.py risk.py cashier.py auditor.py
│   ├── review.py        regras da revisão das posições abertas (decide, counterfactual, learn_patience)
│   └── playbooks/       equipe.md (manual da equipe), manager.md, news.md, daily.md → prompts da IA
├── core/
│   ├── indicators.py strategies.py   indicadores e as 18 estratégias (REGISTRY, get_strategy)
│   ├── backtest.py metrics.py        backtest honesto e métricas (inclui avg_minutes e horizon)
│   ├── evaluation.py                 variantes, walk-forward, evolução (HORIZON_LABELS)
│   ├── horizons.py                   scalper / day trade / posição longa
│   ├── risk.py assets.py bars.py     lote, exposição, moedas do ativo, candles
├── broker/
│   ├── mt5.py           cliente HTTP do bridge (token)
│   ├── terminals.py     terminais MT5 cadastrados (um ativo por vez)
│   ├── market.py        MarketService: MT5, preços reais públicos (realdata.py) ou mercado simulado (synthetic.py)
│   ├── realdata.py      RealMarket: Yahoo Finance + Binance (route_for, cache incremental, tick, spec, atrasos)
│   └── execution.py     PaperBroker (simulado) e LiveBroker (MT5)
├── services/
│   ├── llm.py           LLMService (OpenRouter, modelos fixos, JSON Schema, orçamento)
│   └── feeds.py         RSS e calendário (Forex Factory)
└── api/                 agents, auth, daily, market, settings, strategies, system, trades, ws
backend/tests/           pytest (conftest: banco limpo por teste; fixtures office, client_owner)

backend/scripts/verificar_sistema.py   escritório inteiro com preços reais por N minutos + relatório (usado no workflow verificacao.yml)

frontend/src/
├── App.tsx              rotas: / (Escritório), /agentes, /daily, /estrategias, /operacoes, /config
├── main.tsx             React Query, AuthProvider, registro do service worker (produção)
├── lib/live.ts          WebSocket /ws + store (agents, system, office, activity, messages, lastDaily)
├── lib/api.ts format.ts auth.tsx
├── components/          Layout (menu, chips, instalar app), ui (Card, Badge, Field…), Chat (conversa), Charts
├── office/              engine.ts (motor: mundo + sobreposição), OfficeCanvas.tsx, map.ts, sprites.ts
└── pages/               Office, Agents, Daily, Strategies, Trades, Settings, Login
frontend/public/         manifest.webmanifest, sw.js, icons/ (PWA), favicon.svg
frontend/nginx/          proxy /api e /ws, cabeçalhos de segurança, cache do sw.js
frontend/e2e/smoke.mjs   teste de ponta a ponta (Playwright)

mt5/                     bridge HTTP (mt5/Metatrader/bridge/metabot_bridge.py), kit Windows, imagem Docker
deploy/portainer-stack.yml   stack de produção (Swarm + Traefik, Postgres compartilhado)
deploy/mt5-remoto.yml        MT5 em Docker numa máquina Linux Intel/AMD
.github/workflows/ci.yml     testes, e2e, imagens GHCR (backend/frontend multi-arch, mt5 amd64)
```

## Tabelas

`users`, `auth_sessions`, `secrets`, `kv`, `terminals`, `activity`, `agent_messages` (conversa), `daily_reports` (um por dia: transcript, sections, adjustments, lessons, focus, metrics), `ai_usage`, `skills`, `skill_events`, `lessons` (`source`: auditor/daily), `news`, `calendar_events`, `strategy_profiles` (status: nova/aprovada/reprovada/observacao; metrics/oos_metrics/live JSON), `backtest_runs`, `decisions`, `signals`, `trades`, `equity`.

## Chaves no kv

| Chave | Quem usa | Conteúdo |
|---|---|---|
| `runtime_config` | runtime.py | configuração da tela (validada por RuntimeConfig) |
| `risk_state` | Rita | dia atual, patrimônio inicial do dia, day_blocked/day_stop, pico, trava, adaptive_mult |
| `team.avoid_hours` | daily → Gustavo | `{ativo ou "*": {"hours_utc": [...], "until": ISO UTC}}` |
| `manager.weights` | Gustavo | confiança em estratégia/hora/notícia/real |
| `manager.horizon_weights` | Gustavo | peso de scalp/day/swing (0,75–1,25) |
| `daily.last` / `daily.focus_told` | daily / Gustavo | último dia com daily; dia em que o foco foi lembrado |
| `office.break` | office (pausa depois da daily) | `{"until": ISO UTC, "started", "minutes"}` ou nulo |
| `manager.review_patience` | Gustavo (revisão) | paciência aprendida (−0,3 a +0,3): maior = espera mais antes de fechar |
| `manager.review_stats` / `manager.review_day` | Gustavo → daily | saídas conferidas (acertos/cedo) e contagem do dia (close/sl/tp/hold) |
| `ai_status` | llm.py | última chamada (ok ou erro) mostrada na tela |
| `paper_reset_at` | sistema | quando a conta simulada foi zerada |
| `market.family` | office.sync_data_family | `real` ou `simulado`: origem dos preços com que a equipe aprendeu |

Segredos (criptografados): `openrouter_api_key` e credenciais de terminais.

## Eventos do WebSocket (`bus.publish`)

`snapshot` (ao conectar: agents, system, office, activity, messages), `agent` (estado/tarefa), `activity`, `message` (sender, recipient, kind, text), `say` (balão), `meeting` (host, participants, lines, title), `daily` (day, summary, pnl, mood), `office` (dados do painel: risk, plan, account, headline, office_break…), `trade`, `levelup`, `account`, `mt5`, `system` (running, quando a pausa fecha/reabre ou o dono liga/desliga).

## API (todas exigem login, menos health e auth)

| Grupo | Rotas |
|---|---|
| Sistema | `GET /api/health`, `GET /api/system`, `POST /api/system/running`, `POST /api/system/mode`, `POST /api/system/kill-switch/reset`, `POST /api/system/paper/reset` |
| Autenticação | `/api/auth/status`, `setup`, `login`, `logout`, `me`, `password`, `forward`, `2fa/setup`, `2fa/enable`, `2fa/disable` |
| Agentes | `GET /api/agents`, `GET /api/agents/{id}`, `POST /api/agents/{id}/run?job=`, `GET /api/activity`, `GET /api/messages?limit&agent`, `GET/DELETE /api/lessons` |
| Daily | `GET /api/daily`, `GET /api/daily/{dia}`, `POST /api/daily/run` |
| Estratégias | `GET /api/strategies`, `ranking`, `horizons`, `profiles/{id}`, `signals`, `POST ranking/run`, `evolution/run`, `backtest` |
| Mercado | `GET /api/market/symbols`, `candles`, `overview`, `hours`, `GET /api/news`, `GET /api/calendar` |
| Operações | `GET /api/trades`, `trades/open`, `trades/summary`, `POST /api/trades/{id}/close`, `GET /api/decisions` |
| Config. | `GET/PUT /api/settings`, `POST /api/settings/ai-key`, terminais em `/api/settings/terminals` (+ `test`) |
| Tempo real | `WS /ws` |

## Configuração (RuntimeConfig) por área

- **Metas do dia:** `daily_loss_limit` + `daily_loss_unit` (percent|money), `daily_profit_target` + `daily_profit_unit`, `close_on_daily_limit`.
- **Risco (Rita):** `risk_per_trade_pct`, `max_drawdown_pct`, `max_open_positions`, `max_positions_per_symbol`, `max_currency_exposure`, `max_spread_multiplier`, `adaptive_risk`, `min_lot_overrisk`.
- **Estratégias (Estela):** `watchlist`, `timeframes`, `enabled_strategies`, `rank_by`, `min_trades`, `min_profit_factor`, `oos_fraction`, `ranking_interval_hours`, `evolution_enabled`, `evolution_interval_hours`.
- **Gerente (Gustavo):** `decision_interval_minutes`, `max_active_setups`, `min_hour_quality`, `use_news_filter`, `news_block_threshold`, `position_review_minutes` (revisão das posições abertas; 0 = desligada), `ai_plan_refresh_minutes`.
- **Caixa (Caio):** `break_even_r`, `trailing_start_r`, `trailing_atr_mult`, `adaptive_exits`, `max_bars_in_trade`, `close_before_weekend`, `b3_close_time`, `b3_prefixes`.
- **Hugo:** `blackout_before_min`, `blackout_after_min`, `blackout_impacts`, `calendar_url`.
- **Nina:** `news_enabled`, `news_interval_minutes`, `news_feeds`, `ai_news_interval_minutes`.
- **Daily:** `daily_meeting_enabled`, `daily_meeting_time`, `daily_break_minutes` (escritório fechado depois da daily automática; 0 = sem pausa).
- **IA:** `ai_enabled`, `ai_max_calls_per_hour`, `ai_daily_budget_usd` (modelos não são configuráveis).
- **Simulado/MT5:** `paper_initial_balance`, `paper_commission_per_lot`, `paper_slippage_points`, `data_source` (auto | real | mt5 | synthetic), `magic_number`, `deviation_points`, `server_utc_offset_hours`.
- **Sistema (rotas próprias, com senha):** `system_running`, `mode`.

A tela (`frontend/src/pages/Settings.tsx`) descreve cada campo em `SECTIONS`/`SIMPLE_FIELDS`; `tests/test_settings_effects.py` prova que os principais mudam o comportamento.

## Variáveis de ambiente (backend)

`MB_ENV`, `MB_SECRET_KEY`, `MB_DATABASE_URL`, `MB_DATA_DIR`, `MB_ADMIN_EMAIL`, `MB_ADMIN_PASSWORD`, `MB_COOKIE_SECURE`, `MB_PUBLIC_URL`, `MB_ALLOWED_ORIGINS`, `MB_MT5_BRIDGE_URL`, `MB_MT5_BRIDGE_TOKEN`, `MB_MT5_PANEL_URL`, `MB_OPENROUTER_API_KEY`, `MB_TIMEZONE` (padrão America/Sao_Paulo), `MB_NETWORK_ENABLED` (padrão true: preços reais, notícias e calendário). Nos testes: `MB_AGENTS_ENABLED=false`, `MB_NETWORK_ENABLED=false`.
