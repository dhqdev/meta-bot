# Receitas do Meta-Bot

Passo a passo das mudanças mais comuns. Sempre termine com: testes do backend, `npm run build` no frontend e, se mexeu em tela, o e2e.

## Estratégia nova

1. Em `backend/app/core/strategies.py`, escreva `_minha(b: Bars, p: dict) -> SignalSet` usando só dados até o candle atual (sem olhar o futuro; use os helpers de `app/core/indicators.py`).
2. Adicione um `Strategy(key, nome, origem, família, descrição, [P(...)], _minha, {"sl_atr": ..., "tp_r": ...}, nota_de_saída)` em `STRATEGIES`.
3. O teste `tests/test_indicators_strategies.py` já confere "sem olhar o futuro" para todas as estratégias de `STRATEGIES`; rode-o. A Estela passa a testar a estratégia no próximo ranking, a tela de Estratégias e o catálogo mostram sozinhos.

## Campo novo na tela de Config.

1. `backend/app/runtime.py`: campo em `RuntimeConfig` com `Field(padrão, ge=..., le=...)` e comentário curto. Se mudar o significado de um campo antigo, suba `CONFIG_VERSION` e converta em `_migrate`.
2. Use o campo onde importa (`get_config().meu_campo`). Se o efeito precisar ser recalculado na hora, trate em `put_settings` (`backend/app/api/settings.py`), como `invalidate_exit_params` ou `request("guard")`.
3. Nome amigável para erros: `FIELD_LABELS` em `backend/app/api/settings.py`.
4. Tela: em `frontend/src/pages/Settings.tsx`, adicione um `FieldDef` na seção do agente (`SECTIONS`) ou no topo (`SIMPLE_FIELDS`), com `hint` explicando em português simples o que muda na prática. O "padrão" e o "voltar para o padrão" vêm de `defaults` da API, automaticamente.
5. Teste em `tests/test_settings_effects.py` provando que o campo muda o comportamento.

## Fala ou mensagem nova de um agente

1. `backend/app/agents/personas.py`: adicione a chave em `LINES[agente]` com 2 variações no jeito do agente (use `{campos}`; sempre inclua o ativo/tempo gráfico quando fizer sentido).
2. No agente: `self.tell("destino", "emoji " + self.line("chave", campo=valor), kind="pedido|resposta|alerta|comemoracao|info|daily")`. Para conversa de duas vias, o destinatário responde com outro `tell` (ver `office._pipeline`, `news.py`, `schedule.py`).
3. Nada a fazer no front: a aba Conversa, o envelope voando e o balão no escritório aparecem sozinhos pelo evento `message`.

## Ajuste automático novo na daily

1. Em `DailyMeeting.analyze()` (`backend/app/agents/daily.py`), na seção do agente responsável: calcule a partir de `data` (números do dia), aplique o ajuste **com limite** e registre `adjustments.append({"agent": ..., "kind": ..., "text": ...})` e, se for lição, `lessons.append(...)`.
2. Guarde o estado em kv com validade (ex.: `until` em ISO UTC) e faça o agente que usa o ajuste respeitá-lo (ex.: `manager.build_candidates` lê `team.avoid_hours`).
3. Nunca aumente risco; prefira reduzir, pausar ou revalidar. Teste em `tests/test_daily.py`.
4. Se o tipo (`kind`) for novo, dê um nome amigável em `ADJ_KIND` (`frontend/src/pages/Daily.tsx`).

## Agente usando IA

Use `await self.office.llm.complete_json(agent=self.id, purpose="...", tier="news|manager|daily", system=..., user=..., schema_model=MeuModelo)`. O modelo vem do `tier` (fixo em `AGENT_MODELS`); trate `res.ok`/`res.data` e tenha sempre um caminho só com regras quando a IA falhar ou estiver sem chave. No prompt: `playbook("...")`, `persona_prompt([...])` e `active_lessons(...)`. Dado externo (notícias) entra como dado, nunca como instrução.

## Tela nova no frontend

1. Página em `frontend/src/pages/`, título `<h1 className="font-pixel text-sm text-gold">TÍTULO</h1>` (o e2e procura o heading).
2. Rota em `App.tsx` e item no `NAV` de `components/Layout.tsx` (o menu de baixo do celular tem 6 colunas: troque um item ou ajuste o grid).
3. Dados: `useQuery` + `api.get`; tempo real: `useLive()` ou `onLiveEvent`.
4. Celular: teste em 390 px de largura sem rolagem lateral (o e2e confere). Fonte pixel (`font-pixel`) só em títulos curtos.
5. Inclua a tela no `pages` do `frontend/e2e/smoke.mjs`.

## Publicar

1. Commit em português na `main` (ou PR). O CI testa e publica `ghcr.io/dhqdev/meta-bot-backend|frontend:latest` (multi-arch, o servidor é ARM).
2. No Portainer (stack do Meta-Bot): *Update the stack → Re-pull image*, ou webhook no secret `PORTAINER_WEBHOOK_URL`.
3. Variável nova de ambiente: documente em `.env.example`, `docker-compose.yml`, `deploy/portainer-stack.yml` e no README, sem valores reais.

## Ativo novo com preço real

`route_for()` em `backend/app/broker/realdata.py` decide a fonte pelo nome (pares de moedas, cripto, aliases de índices, ações da B3). Para um ativo especial, adicione um alias em `YAHOO_ALIASES` (com o atraso conhecido) e, se for um contrato com tamanho próprio, um `Preset` em `EXTRA_PRESETS`. Teste em `tests/test_realdata.py` com a `FakeApis` e confira ao vivo pelo workflow **Verificação com preços reais**.
