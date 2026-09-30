import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, Save, Trash2 } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState, type ReactNode } from "react";
import { Badge, Button, Card, ErrorBox, Field, Input, Loading, PasswordPrompt, Select, Switch } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { patchSystem, useLive } from "../lib/live";
import { money } from "../lib/format";

type Cfg = Record<string, any>;

export function SettingsPage() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<any>("/api/settings") });
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => api.get<any>("/api/strategies") });
  const [draft, setDraft] = useState<Cfg | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const qc = useQueryClient();
  useEffect(() => {
    if (q.data && !draft) setDraft(q.data.config);
  }, [q.data, draft]);
  if (q.isLoading || !draft) return <Loading />;
  const cfg = draft;
  const set = (k: string, v: any) => {
    setDraft({ ...cfg, [k]: v });
    setSaved(false);
  };
  const changed = JSON.stringify(cfg) !== JSON.stringify(q.data.config);
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const patch: Cfg = {};
      for (const k of Object.keys(cfg)) if (JSON.stringify(cfg[k]) !== JSON.stringify(q.data.config[k]) && k !== "mode" && k !== "system_running") patch[k] = cfg[k];
      const res = await api.put<any>("/api/settings", patch);
      setDraft(res.config);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };
  const num = (k: string, step = 0.1) => <Input type="number" step={step} value={cfg[k] ?? ""} onChange={(e) => set(k, e.target.value === "" ? null : Number(e.target.value))} />;

  return (
    <div className="space-y-4 pb-16">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-pixel text-sm text-gold">CONFIGURAÇÕES</h1>
          <p className="mt-1 text-sm text-muted">Tudo aqui fica salvo no banco e vale na hora para os agentes.</p>
        </div>
      </div>

      <ModeCard />
      <MT5Card />

      <Card title="Ativos e tempos gráficos">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Ativos (nomes exatos da sua corretora)" hint="Ex.: EURUSD, XAUUSD, US500, BTCUSD, WIN$N, WDO$N. Cada corretora usa sufixos próprios (EURUSDm, EURUSD.a...): confira no MT5.">
            <WatchlistEditor value={cfg.watchlist} onChange={(v) => set("watchlist", v)} />
          </Field>
          <Field label="Tempos gráficos testados pela Estela">
            <div className="flex flex-wrap gap-2">
              {["M5", "M15", "M30", "H1", "H4", "D1"].map((tf) => (
                <label key={tf} className="flex items-center gap-1.5 rounded-md border border-line px-2 py-1 text-sm">
                  <input type="checkbox" checked={cfg.timeframes.includes(tf)} onChange={(e) => set("timeframes", e.target.checked ? [...cfg.timeframes, tf] : cfg.timeframes.filter((x: string) => x !== tf))} /> {tf}
                </label>
              ))}
            </div>
          </Field>
        </div>
        <div className="mt-4">
          <Field label="Estratégias ligadas" hint="Nenhuma marcada = todas.">
            <div className="flex flex-wrap gap-1.5">
              {catalog.data?.strategies.map((s: any) => {
                const on = cfg.enabled_strategies.includes(s.key);
                return (
                  <button key={s.key} type="button" onClick={() => set("enabled_strategies", on ? cfg.enabled_strategies.filter((k: string) => k !== s.key) : [...cfg.enabled_strategies, s.key])} className={`rounded-md border px-2 py-1 text-xs ${on ? "border-gold bg-gold/15 text-gold" : "border-line text-slate-300"}`}>
                    {s.name}
                  </button>
                );
              })}
            </div>
          </Field>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Risco (Rita)">
          <Grid>
            <Field label="Risco por operação (%)" hint="Quanto do patrimônio cada operação arrisca até o stop.">{num("risk_per_trade_pct", 0.05)}</Field>
            <Field label="Perda diária máxima (%)" hint="Atingiu: sem novas entradas até o dia seguinte.">{num("max_daily_loss_pct")}</Field>
            <Field label="Queda máxima desde o pico (%)" hint="Atingiu: trava geral até você liberar.">{num("max_drawdown_pct")}</Field>
            <Field label="Posições simultâneas">{num("max_open_positions", 1)}</Field>
            <Field label="Posições por ativo">{num("max_positions_per_symbol", 1)}</Field>
            <Field label="Exposição máxima por moeda" hint="Ex.: comprar EURUSD e GBPUSD = −2 em USD.">{num("max_currency_exposure", 1)}</Field>
            <Field label="Spread máximo (× o normal)">{num("max_spread_multiplier")}</Field>
            <Field label="Aceitar lote mínimo até (× o risco)">{num("min_lot_overrisk")}</Field>
          </Grid>
          <div className="mt-3">
            <Switch checked={cfg.adaptive_risk} onChange={(v) => set("adaptive_risk", v)} label="Risco adaptativo (reduz depois de perdas seguidas)" />
          </div>
        </Card>
        <Card title="Estrategista (Estela)">
          <Grid>
            <Field label="Ordenar o ranking por">
              <Select value={cfg.rank_by} onChange={(v) => set("rank_by", v)} options={[["win_rate", "Taxa de acerto"], ["expectancy", "Expectativa"], ["profit_factor", "Fator de lucro"], ["net", "Resultado/risco"]]} />
            </Field>
            <Field label="Mínimo de operações no teste">{num("min_trades", 1)}</Field>
            <Field label="Fator de lucro mínimo">{num("min_profit_factor", 0.05)}</Field>
            <Field label="Parte recente (fora da amostra)" hint="0,3 = últimos 30% do histórico.">{num("oos_fraction", 0.05)}</Field>
            <Field label="Refazer o ranking a cada (h)">{num("ranking_interval_hours", 0.5)}</Field>
            <Field label="Evoluir a cada (h)">{num("evolution_interval_hours", 1)}</Field>
          </Grid>
          <div className="mt-3">
            <Switch checked={cfg.evolution_enabled} onChange={(v) => set("evolution_enabled", v)} label="Evolução automática das estratégias" />
          </div>
        </Card>
        <Card title="Gerente (Gustavo)">
          <Grid>
            <Field label="Decidir a cada (min)">{num("decision_interval_minutes", 1)}</Field>
            <Field label="Setups ativos ao mesmo tempo">{num("max_active_setups", 1)}</Field>
            <Field label="Qualidade mínima da hora (0 a 1)">{num("min_hour_quality", 0.05)}</Field>
            <Field label="Força de notícia que veta (0 a 1)">{num("news_block_threshold", 0.05)}</Field>
          </Grid>
          <div className="mt-3">
            <Switch checked={cfg.use_news_filter} onChange={(v) => set("use_news_filter", v)} label="Usar as notícias da Nina para direção e veto" />
          </div>
        </Card>
        <Card title="Caixa (Caio)">
          <Grid>
            <Field label="Zero a zero a partir de (R)" hint="0 desliga.">{num("break_even_r")}</Field>
            <Field label="Trailing a partir de (R)" hint="0 desliga.">{num("trailing_start_r")}</Field>
            <Field label="Distância do trailing (ATR)">{num("trailing_atr_mult")}</Field>
            <Field label="Máx. candles na operação" hint="0 = padrão do tempo gráfico.">{num("max_bars_in_trade", 1)}</Field>
            <Field label="Fechar day trade B3 às">
              <Input value={cfg.b3_close_time} onChange={(e) => set("b3_close_time", e.target.value)} />
            </Field>
            <Field label="Prefixos B3 (day trade)">
              <Input value={cfg.b3_prefixes.join(", ")} onChange={(e) => set("b3_prefixes", e.target.value.split(",").map((x) => x.trim()).filter(Boolean))} />
            </Field>
          </Grid>
          <div className="mt-3 space-y-2">
            <Switch checked={cfg.adaptive_exits} onChange={(v) => set("adaptive_exits", v)} label="Usar a gestão de saída que o Caio aprender" />
            <Switch checked={cfg.close_before_weekend} onChange={(v) => set("close_before_weekend", v)} label="Fechar posições antes do fim de semana (sexta 20h45 UTC)" />
          </div>
        </Card>
        <Card title="Horários e calendário (Hugo)">
          <Grid>
            <Field label="Pausa antes do evento (min)">{num("blackout_before_min", 5)}</Field>
            <Field label="Pausa depois do evento (min)">{num("blackout_after_min", 5)}</Field>
          </Grid>
          <div className="mt-3 flex gap-3 text-sm">
            {["High", "Medium"].map((imp) => (
              <label key={imp} className="flex items-center gap-1.5">
                <input type="checkbox" checked={cfg.blackout_impacts.includes(imp)} onChange={(e) => set("blackout_impacts", e.target.checked ? [...cfg.blackout_impacts, imp] : cfg.blackout_impacts.filter((x: string) => x !== imp))} />
                pausar em eventos de impacto {imp === "High" ? "alto" : "médio"}
              </label>
            ))}
          </div>
        </Card>
        <Card title="Conta simulada">
          <Grid>
            <Field label="Saldo inicial (USD)">{num("paper_initial_balance", 100)}</Field>
            <Field label="Comissão por lote (ida e volta)" hint="Também entra nos backtests.">{num("paper_commission_per_lot", 0.5)}</Field>
            <Field label="Slippage (pontos)" hint="Também entra nos backtests.">{num("paper_slippage_points", 1)}</Field>
            <Field label="Origem dos dados">
              <Select value={cfg.data_source} onChange={(v) => set("data_source", v)} options={[["auto", "Automático (MT5 se conectado)"], ["mt5", "Sempre MT5"], ["synthetic", "Sempre simulado"]]} />
            </Field>
          </Grid>
        </Card>
      </div>

      <NewsFeedsCard feeds={cfg.news_feeds} onChange={(v) => set("news_feeds", v)} enabled={cfg.news_enabled} onEnabled={(v) => set("news_enabled", v)} interval={num("news_interval_minutes", 1)} />
      <AICard cfg={cfg} set={set} options={q.data.options} ai={q.data.ai} />
      <SecurityCard />

      <div className="fixed inset-x-0 bottom-16 z-20 flex justify-center px-3 lg:bottom-4">
        {(changed || saved || !!error) && (
          <div className="flex items-center gap-3 rounded-xl border border-line bg-panel px-4 py-2 shadow-2xl">
            {error ? <ErrorBox error={error} /> : <span className="text-sm text-muted">{saved && !changed ? "Salvo ✓" : "Alterações não salvas"}</span>}
            <Button onClick={save} loading={saving} disabled={!changed}>
              <Save className="h-4 w-4" /> Salvar
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

function Grid({ children }: { children: ReactNode }) {
  return <div className="grid gap-3 sm:grid-cols-2">{children}</div>;
}

function WatchlistEditor({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState("");
  const search = useQuery({ queryKey: ["symbols", text], queryFn: () => api.get<any[]>(`/api/market/symbols?q=${encodeURIComponent(text)}`), enabled: text.length >= 2 });
  const add = (s: string) => {
    const sym = s.trim();
    if (sym && !value.includes(sym)) onChange([...value, sym]);
    setText("");
  };
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {value.map((s) => (
          <Badge key={s} tone="blue" className="text-xs">
            {s}
            <button type="button" onClick={() => onChange(value.filter((x) => x !== s))} className="ml-1 text-sky-200 hover:text-white">
              ×
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input value={text} onChange={(e) => setText(e.target.value.toUpperCase())} list="wl-symbols" placeholder="Buscar ou digitar o ativo" onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add(text))} />
        <datalist id="wl-symbols">
          {search.data?.map((s) => (
            <option key={s.name} value={s.name}>
              {s.description}
            </option>
          ))}
        </datalist>
        <Button type="button" variant="ghost" onClick={() => add(text)}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function ModeCard() {
  const live = useLive();
  const [prompt, setPrompt] = useState<null | "live" | "kill" | "paper-reset">(null);
  const risk = live.office.risk || {};
  const mode = live.system.mode || "paper";
  return (
    <Card title="Modo de operação">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={mode === "live" ? "gold" : "blue"} className="text-sm">
          {mode === "live" ? "Conta da corretora (MT5)" : "Conta simulada"}
        </Badge>
        {mode === "paper" ? (
          <Button variant="primary" onClick={() => setPrompt("live")}>
            Usar a conta do MT5
          </Button>
        ) : (
          <Button
            variant="ghost"
            onClick={async () => {
              const r = await api.post<any>("/api/system/mode", { mode: "paper" });
              patchSystem({ mode: r.mode });
            }}
          >
            Voltar para o simulado
          </Button>
        )}
        <Button variant="ghost" onClick={() => setPrompt("paper-reset")}>
          Zerar conta simulada
        </Button>
        {risk.kill_switch && (
          <Button variant="danger" onClick={() => setPrompt("kill")}>
            Liberar trava geral
          </Button>
        )}
      </div>
      <p className="mt-3 text-xs text-muted">
        No modo conta do MT5, as ordens vão para a conta logada no terminal (use primeiro uma <b>conta demo</b> da sua corretora). No simulado, o sistema usa preços do MT5 (ou do mercado simulado) sem enviar ordens.
      </p>
      <PasswordPrompt
        open={prompt === "live"}
        title="Operar na conta do MT5"
        description={<p>As ordens passarão a ser enviadas à corretora logada no MetaTrader 5. Recomendo começar com uma conta demo. Confirme com sua senha.</p>}
        confirmLabel="Entendi, usar a conta"
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          const r = await api.post<any>("/api/system/mode", { mode: "live", password, confirm: true });
          patchSystem({ mode: r.mode, data_source: "mt5" });
          setPrompt(null);
        }}
      />
      <PasswordPrompt
        open={prompt === "kill"}
        title="Liberar a trava geral"
        description={<p>A trava foi acionada porque a queda desde o pico passou do limite ({risk.kill_reason}). Liberar permite novas entradas.</p>}
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          await api.post("/api/system/kill-switch/reset", { password });
          setPrompt(null);
        }}
      />
      <PasswordPrompt
        open={prompt === "paper-reset"}
        title="Zerar a conta simulada"
        description={<p>O saldo simulado volta ao valor inicial (o histórico continua guardado).</p>}
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          await api.post("/api/system/paper/reset", { password });
          setPrompt(null);
        }}
      />
    </Card>
  );
}

interface TerminalRow {
  id: number;
  name: string;
  bridge_url: string;
  token_set: boolean;
  login: string;
  server: string;
  broker_password_set: boolean;
  active: boolean;
}

function MT5Card() {
  const live = useLive();
  const q = useQuery({ queryKey: ["terminals"], queryFn: () => api.get<TerminalRow[]>("/api/settings/terminals") });
  const [edit, setEdit] = useState<Partial<TerminalRow> & { token?: string; broker_password?: string } | null>(null);
  const [test, setTest] = useState<any>(null);
  const [prompt, setPrompt] = useState(false);
  const mt5 = live.system.mt5 || {};
  return (
    <Card title="MetaTrader 5 · qualquer corretora" actions={<a href="/mt5/" target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-sky hover:underline">abrir o terminal <ExternalLink className="h-3 w-3" /></a>}>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={mt5.connected ? "green" : mt5.configured ? "red" : "slate"}>{mt5.connected ? "conectado" : mt5.configured ? "desconectado" : "não configurado"}</Badge>
        {mt5.connected && (
          <span className="text-slate-300">
            conta {mt5.login} · {mt5.server} · {mt5.company} · saldo {money(mt5.balance)} {mt5.currency} · fuso UTC{mt5.offset_hours >= 0 ? "+" : ""}
            {mt5.offset_hours}
          </span>
        )}
        {!mt5.connected && mt5.message && <span className="text-muted">{mt5.message}</span>}
      </div>
      <div className="space-y-2">
        {q.data?.map((t) => (
          <div key={t.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
            <b>{t.name}</b>
            {t.active && <Badge tone="green">ativo</Badge>}
            <span className="text-xs text-muted">{t.bridge_url}</span>
            <span className="text-xs text-muted">{t.login ? `conta ${t.login} · ${t.server}` : "login feito pela tela do MT5"}</span>
            {!t.token_set && <Badge tone="red">sem token</Badge>}
            <div className="ml-auto flex gap-1">
              <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setEdit({ ...t, token: "", broker_password: "" })}>
                Editar
              </Button>
              <Button
                variant="ghost"
                className="px-2 py-1 text-xs"
                onClick={async () => setTest(await api.post("/api/settings/terminals/test", { bridge_url: t.bridge_url, terminal_id: t.id }))}
              >
                Testar
              </Button>
            </div>
          </div>
        ))}
        <Button variant="subtle" className="text-xs" onClick={() => setEdit({ name: "MT5 corretora 2", bridge_url: "http://mt5_2:8001", active: false })}>
          <Plus className="h-3.5 w-3.5" /> Adicionar terminal (outra corretora)
        </Button>
        {test && <div className={`rounded-lg px-3 py-2 text-sm ${test.ok ? "bg-emerald-500/10 text-emerald-200" : "bg-red-500/10 text-red-200"}`}>{test.message}</div>}
      </div>
      <details className="mt-4 text-sm text-slate-300">
        <summary className="cursor-pointer text-gold">Como entrar em qualquer corretora</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs leading-relaxed text-muted">
          <li>
            Abra o terminal em <b>/mt5/</b> (link acima). Em <i>Arquivo → Abrir uma conta</i>, busque o nome da corretora (XP, Clear, Genial, IC Markets, Pepperstone, XM…), escolha o servidor e entre com sua conta. Pronto: o servidor fica salvo.
          </li>
          <li>Ou preencha conta, senha e servidor no terminal abaixo: o Tito faz o login pelo bridge (senha criptografada no banco).</li>
          <li>Corretora que distribui MT5 próprio: use a variável <code>MT5_INSTALLER_URL</code> no container mt5 antes da primeira inicialização.</li>
          <li>Várias corretoras ao mesmo tempo: suba um container mt5 por conta e cadastre cada um aqui; o ativo é o que o sistema usa.</li>
        </ol>
      </details>
      {edit && (
        <div className="mt-4 space-y-3 rounded-lg border border-line p-3">
          <Grid>
            <Field label="Nome">
              <Input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
            </Field>
            <Field label="Endereço do bridge" hint="Na stack: http://mt5:8001">
              <Input value={edit.bridge_url ?? ""} onChange={(e) => setEdit({ ...edit, bridge_url: e.target.value })} />
            </Field>
            <Field label="Token do bridge" hint={edit.token_set ? "Deixe em branco para manter o atual." : "O mesmo MT5_BRIDGE_TOKEN do container."}>
              <Input type="password" value={edit.token ?? ""} onChange={(e) => setEdit({ ...edit, token: e.target.value })} />
            </Field>
            <Field label="Conta (número)" hint="Opcional: login automático.">
              <Input value={edit.login ?? ""} onChange={(e) => setEdit({ ...edit, login: e.target.value })} />
            </Field>
            <Field label="Servidor" hint="Exatamente como no MT5, ex.: XPMT5-DEMO">
              <Input value={edit.server ?? ""} onChange={(e) => setEdit({ ...edit, server: e.target.value })} />
            </Field>
            <Field label="Senha da conta na corretora" hint={edit.broker_password_set ? "Deixe em branco para manter." : "Criptografada no banco."}>
              <Input type="password" value={edit.broker_password ?? ""} onChange={(e) => setEdit({ ...edit, broker_password: e.target.value })} />
            </Field>
          </Grid>
          <Switch checked={!!edit.active} onChange={(v) => setEdit({ ...edit, active: v })} label="Usar este terminal" />
          <div className="flex justify-between">
            {edit.id ? (
              <Button
                variant="ghost"
                className="text-down"
                onClick={async () => {
                  const password = window.prompt("Confirme com sua senha para remover o terminal");
                  if (!password) return;
                  await api.del(`/api/settings/terminals/${edit.id}`, { password });
                  setEdit(null);
                  void q.refetch();
                }}
              >
                <Trash2 className="h-4 w-4" /> Remover
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setEdit(null)}>
                Cancelar
              </Button>
              <Button onClick={() => setPrompt(true)}>Salvar terminal</Button>
            </div>
          </div>
        </div>
      )}
      <PasswordPrompt
        open={prompt}
        title="Salvar terminal"
        description="Os dados de acesso ficam criptografados no banco."
        onCancel={() => setPrompt(false)}
        onConfirm={async (password) => {
          const body = { name: edit?.name, bridge_url: edit?.bridge_url, token: edit?.token || null, login: edit?.login || null, server: edit?.server || null, broker_password: edit?.broker_password || null, active: !!edit?.active, password };
          if (edit?.id) await api.put(`/api/settings/terminals/${edit.id}`, body);
          else await api.post("/api/settings/terminals", body);
          setPrompt(false);
          setEdit(null);
          void q.refetch();
        }}
      />
    </Card>
  );
}

function NewsFeedsCard({ feeds, onChange, enabled, onEnabled, interval }: { feeds: any[]; onChange: (v: any[]) => void; enabled: boolean; onEnabled: (v: boolean) => void; interval: ReactNode }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  return (
    <Card title="Notícias (Nina)">
      <div className="mb-3 flex flex-wrap items-end gap-4">
        <Switch checked={enabled} onChange={onEnabled} label="Ler notícias" />
        <div className="w-44">
          <Field label="A cada (min)">{interval}</Field>
        </div>
      </div>
      <div className="space-y-1.5">
        {feeds.map((f, i) => (
          <div key={f.url} className="flex items-center gap-2 text-sm">
            <Switch checked={f.enabled} onChange={(v) => onChange(feeds.map((x, j) => (j === i ? { ...x, enabled: v } : x)))} />
            <b className="w-44 truncate">{f.name}</b>
            <span className="flex-1 truncate text-xs text-muted">{f.url}</span>
            <Badge>{f.lang}</Badge>
            <button className="text-muted hover:text-down" onClick={() => onChange(feeds.filter((_, j) => j !== i))}>
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Input placeholder="Nome" value={name} onChange={(e) => setName(e.target.value)} className="w-40" />
        <Input placeholder="https://…/rss" value={url} onChange={(e) => setUrl(e.target.value)} className="min-w-60 flex-1" />
        <Button
          variant="ghost"
          onClick={() => {
            if (!name || !url.startsWith("http")) return;
            onChange([...feeds, { name, url, lang: /\.br\b|br\./.test(url) ? "pt" : "en", enabled: true }]);
            setName("");
            setUrl("");
          }}
        >
          <Plus className="h-4 w-4" /> Adicionar
        </Button>
      </div>
    </Card>
  );
}

function AICard({ cfg, set, options, ai }: { cfg: Cfg; set: (k: string, v: any) => void; options: any; ai: any }) {
  const [key, setKey] = useState("");
  const [prompt, setPrompt] = useState<null | "save" | "remove">(null);
  const [msg, setMsg] = useState<string | null>(null);
  const qc = useQueryClient();
  const models: Array<[string, string]> = options.ai_models.map((m: string) => {
    const p = options.ai_prices[m];
    return [m, p ? `${m} (US$ ${p[0]}/${p[1]} por milhão de tokens)` : m];
  });
  return (
    <Card title="Inteligência artificial (Claude)">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        {ai.key_set ? <Badge tone="green">chave cadastrada {ai.key_masked}</Badge> : <Badge tone="red">sem chave: agentes usam regras sem IA</Badge>}
        {ai.from_env && <Badge>vinda da variável MB_ANTHROPIC_API_KEY</Badge>}
        <span className="text-xs text-muted">gasto hoje: US$ {money(ai.usage?.today_usd, 4)}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        <Input type="password" placeholder="sk-ant-…" value={key} onChange={(e) => setKey(e.target.value)} className="max-w-sm" />
        <Button onClick={() => setPrompt("save")} disabled={!key}>
          Salvar chave
        </Button>
        {ai.key_set && !ai.from_env && (
          <Button variant="ghost" onClick={() => setPrompt("remove")}>
            Remover
          </Button>
        )}
      </div>
      {msg && <p className="mt-2 text-sm text-emerald-300">{msg}</p>}
      <div className="mt-4">
        <Grid>
          <Field label="Modelo do Gerente e da Auditora" hint="Decisões e diário (poucas chamadas).">
            <Select value={cfg.ai_model} onChange={(v) => set("ai_model", v)} options={models} />
          </Field>
          <Field label="Modelo das notícias (Nina)" hint="Muitas chamadas pequenas: o Haiku 4.5 custa ~4× menos.">
            <Select value={cfg.ai_news_model} onChange={(v) => set("ai_news_model", v)} options={models} />
          </Field>
          <Field label="Máximo de chamadas por hora">
            <Input type="number" value={cfg.ai_max_calls_per_hour} onChange={(e) => set("ai_max_calls_per_hour", Number(e.target.value))} />
          </Field>
          <Field label="Orçamento diário (US$)">
            <Input type="number" step={0.5} value={cfg.ai_daily_budget_usd} onChange={(e) => set("ai_daily_budget_usd", Number(e.target.value))} />
          </Field>
        </Grid>
        <div className="mt-3">
          <Switch checked={cfg.ai_enabled} onChange={(v) => set("ai_enabled", v)} label="IA ligada" />
        </div>
      </div>
      {ai.usage?.by_agent?.length > 0 && (
        <div className="mt-3 text-xs text-muted">
          Últimos 7 dias:{" "}
          {ai.usage.by_agent.map((u: any) => (
            <span key={u.agent} className="mr-3">
              {u.agent}: {u.calls} chamadas · US$ {money(u.cost_usd, 3)}
            </span>
          ))}
        </div>
      )}
      <PasswordPrompt
        open={prompt !== null}
        title={prompt === "remove" ? "Remover a chave" : "Salvar a chave da Anthropic"}
        description={prompt === "remove" ? "Os agentes passam a usar as regras sem IA." : "A chave é conferida na Anthropic (sem gastar tokens) e fica criptografada."}
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          const r = await api.post<any>("/api/settings/ai-key", { api_key: prompt === "remove" ? null : key, password });
          setMsg(r.message);
          setKey("");
          setPrompt(null);
          void qc.invalidateQueries({ queryKey: ["settings"] });
        }}
      />
    </Card>
  );
}

function SecurityCard() {
  const { status, refresh } = useAuth();
  const [pw, setPw] = useState({ current: "", next: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [totp, setTotp] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [prompt, setPrompt] = useState<null | "setup">(null);
  return (
    <Card title="Segurança">
      <div className="grid gap-6 md:grid-cols-2">
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            try {
              await api.post("/api/auth/password", { current_password: pw.current, new_password: pw.next });
              setMsg("Senha alterada. As outras sessões foram encerradas.");
              setPw({ current: "", next: "" });
            } catch (err) {
              setError(err);
            }
          }}
        >
          <h4 className="text-sm font-semibold">Trocar a senha</h4>
          <Input type="password" placeholder="Senha atual" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" />
          <Input type="password" placeholder="Nova senha" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" />
          <Button type="submit" variant="ghost" disabled={!pw.current || !pw.next}>
            Trocar
          </Button>
          {msg && <p className="text-sm text-emerald-300">{msg}</p>}
          <ErrorBox error={error} />
        </form>
        <div className="space-y-2">
          <h4 className="text-sm font-semibold">Verificação em duas etapas</h4>
          {status?.totp_enabled ? (
            <Badge tone="green">ativa</Badge>
          ) : totp ? (
            <div className="space-y-2">
              <img src={totp.qr} alt="QR code" className="h-40 w-40 rounded bg-white p-2" />
              <p className="break-all text-xs text-muted">Ou digite no app: {totp.secret}</p>
              <div className="flex gap-2">
                <Input inputMode="numeric" placeholder="Código de 6 dígitos" value={code} onChange={(e) => setCode(e.target.value)} className="max-w-40" />
                <Button
                  onClick={async () => {
                    try {
                      await api.post("/api/auth/2fa/enable", { code });
                      setTotp(null);
                      await refresh();
                    } catch (err) {
                      setError(err);
                    }
                  }}
                >
                  Ativar
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="ghost" onClick={() => setPrompt("setup")}>
              Configurar com app autenticador
            </Button>
          )}
        </div>
      </div>
      <PasswordPrompt
        open={prompt === "setup"}
        title="Verificação em duas etapas"
        description="Confirme sua senha para gerar o QR code."
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          const r = await api.post<{ secret: string; uri: string }>("/api/auth/2fa/setup", { password });
          setTotp({ ...r, qr: await QRCode.toDataURL(r.uri, { margin: 1, width: 220 }) });
          setPrompt(null);
        }}
      />
    </Card>
  );
}
