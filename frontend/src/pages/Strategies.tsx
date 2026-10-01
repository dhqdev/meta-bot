import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Dna, FlaskConical, Play, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { LineChart } from "../components/Charts";
import { Badge, Button, Card, Empty, ErrorBox, Field, Input, Loading, Select } from "../components/ui";
import { api } from "../lib/api";
import { ago, dateTime, EXIT_LABEL, num, pct, signed } from "../lib/format";

const RANK_OPTIONS: Array<[string, string]> = [
  ["win_rate", "Taxa de acerto (conservadora)"],
  ["expectancy", "Expectativa por operação"],
  ["profit_factor", "Fator de lucro"],
  ["net", "Resultado ajustado ao risco"],
];
const TFS = ["M5", "M15", "M30", "H1", "H4", "D1"];
const STATUS: Record<string, { label: string; tone: "green" | "red" | "gold" | "slate" }> = {
  aprovada: { label: "aprovada", tone: "green" },
  reprovada: { label: "reprovada", tone: "red" },
  observacao: { label: "em observação", tone: "gold" },
  nova: { label: "nova", tone: "slate" },
};

export function StrategiesPage() {
  const [tab, setTab] = useState<"ranking" | "backtest" | "catalog">("ranking");
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-pixel text-sm text-gold">ESTRATÉGIAS</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            A Estela testa 18 estratégias (setups do Vilela One, indicadores do IndicatorSpot, setups brasileiros e clássicos) em cada ativo e tempo gráfico, com spread, slippage e comissão. Só aprova quem tem amostra suficiente e continua lucrando no período recente que não foi usado para escolher.
          </p>
        </div>
        <div className="flex gap-1 rounded-lg bg-panel p-1">
          {(
            [
              ["ranking", "Ranking"],
              ["backtest", "Backtest"],
              ["catalog", "Catálogo"],
            ] as const
          ).map(([k, l]) => (
            <button key={k} onClick={() => setTab(k)} className={clsx("rounded-md px-3 py-1.5 text-sm", tab === k ? "bg-panel2 text-gold" : "text-slate-300")}>
              {l}
            </button>
          ))}
        </div>
      </div>
      {tab === "ranking" && (
        <>
          <HorizonsCard />
          <Ranking />
        </>
      )}
      {tab === "backtest" && <Backtest />}
      {tab === "catalog" && <Catalog />}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = { mt5: "dados do MT5", real: "preços reais (Yahoo Finance/Binance)", synthetic: "o mercado simulado" };
const HORIZON_LABEL: Record<string, string> = { scalp: "scalper", day: "day trade", swing: "posição longa" };

/** Scalper x day trade x posição longa: o que o backtest e as operações reais mostram, e a preferência aprendida. */
function HorizonsCard() {
  const q = useQuery({ queryKey: ["horizons"], queryFn: () => api.get<any>("/api/strategies/horizons?days=30"), refetchInterval: 60000 });
  const rows: any[] = q.data?.horizons ?? [];
  if (!rows.length) return null;
  const weights: Record<string, number> = q.data?.weights ?? {};
  return (
    <Card title="Scalper x day trade x posição longa">
      <p className="mb-3 text-xs text-muted">
        Quanto tempo a operação fica aberta faz diferença. A Estela testa cada estratégia nos dois estilos — scalper (alvo curto, sai rápido) e segurando mais tempo (alvo maior) — e o Gustavo passa a preferir o que está dando mais resultado. A preferência é recalibrada todo dia na daily, aos poucos.
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        {rows.map((h) => {
          const w = weights[h.key] ?? 1;
          const tone = w > 1.02 ? "green" : w < 0.98 ? "red" : "slate";
          return (
            <div key={h.key} className="rounded-lg border border-line bg-panel2 p-3">
              <div className="flex items-center justify-between gap-2">
                <b>{h.label}</b>
                <Badge tone={tone}>
                  {w > 1.02 ? "preferido" : w < 0.98 ? "menos usado" : "neutro"} · peso {num(w, 2)}
                </Badge>
              </div>
              <div className="text-[11px] text-muted">{h.desc}</div>
              <div className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
                <span className="text-muted">aprovadas</span>
                <span className="text-right tabular-nums">
                  {h.approved} de {h.profiles}
                </span>
                <span className="text-muted">acerto no teste</span>
                <span className="text-right tabular-nums">{h.approved ? pct(h.bt_win_rate) : "—"}</span>
                <span className="text-muted">expectativa na prova</span>
                <span className="text-right tabular-nums">{h.approved ? `${signed(h.bt_oos_expectancy_r)}R` : "—"}</span>
                <span className="text-muted">real (30 dias)</span>
                <span className={clsx("text-right tabular-nums", h.live_r > 0 ? "text-up" : h.live_r < 0 ? "text-down" : "")}>{h.live_trades ? `${h.live_trades} op · ${signed(h.live_r)}R` : "—"}</span>
              </div>
              {h.best && (
                <div className="mt-2 truncate text-[11px] text-slate-300">
                  melhor: {h.best.strategy_name} · {h.best.symbol} {h.best.timeframe}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function Ranking() {
  const [symbol, setSymbol] = useState("");
  const [tf, setTf] = useState("");
  const [onlyApproved, setOnlyApproved] = useState(false);
  const [detail, setDetail] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["ranking", symbol, tf, onlyApproved],
    queryFn: () => api.get<any>(`/api/strategies/ranking?limit=300${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}${tf ? `&timeframe=${tf}` : ""}${onlyApproved ? "&only_approved=true" : ""}`),
    refetchInterval: 20000,
  });
  const rows: any[] = q.data?.profiles || [];
  const [limit, setLimit] = useState(40);
  const symbols = useMemo(() => Array.from(new Set(rows.map((r) => r.symbol))).sort(), [rows]);
  const approved = rows.filter((r) => r.status === "aprovada").length;
  return (
    <Card
      title={`Ranking · ${RANK_OPTIONS.find((o) => o[0] === q.data?.rank_by)?.[1] ?? ""}`}
      actions={
        <>
          <Button variant="ghost" className="text-xs" onClick={() => api.post("/api/strategies/ranking/run").then(() => setTimeout(() => q.refetch(), 3000))}>
            <RefreshCw className={clsx("h-3.5 w-3.5", q.data?.running && "animate-spin")} /> Rodar agora
          </Button>
          <Button variant="ghost" className="text-xs" onClick={() => api.post("/api/strategies/evolution/run")}>
            <Dna className="h-3.5 w-3.5" /> Evoluir agora
          </Button>
        </>
      }
      pad={false}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-3">
        <Select value={symbol} onChange={setSymbol} options={[["", "Todos os ativos"], ...symbols.map((s) => [s, s] as [string, string])]} className="w-40" />
        <Select value={tf} onChange={setTf} options={[["", "Todos os tempos"], ...TFS.map((t) => [t, t] as [string, string])]} className="w-44" />
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input type="checkbox" checked={onlyApproved} onChange={(e) => setOnlyApproved(e.target.checked)} /> só aprovadas
        </label>
        <span className="ml-auto text-xs text-muted">A ordenação fica em Config. → Ajustes finos → Estela.</span>
      </div>
      {q.isLoading ? (
        <Loading />
      ) : rows.length === 0 ? (
        <div className="p-4">
          <Empty>Ainda sem ranking. Ligue o escritório (a Estela roda o ranking ao chegar) ou toque em “Rodar agora”.</Empty>
        </div>
      ) : (
        <div className="scroll-thin overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[11px] uppercase text-muted">
              <tr className="border-b border-line">
                <th className="px-3 py-2">Ativo</th>
                <th className="px-2">Estratégia</th>
                <th className="px-2">Status</th>
                <th className="px-2 text-right">Oper.</th>
                <th className="px-2 text-right">Acerto</th>
                <th className="px-2 text-right" title="Acerto garantido com 95% de confiança (limite de Wilson)">
                  Acerto mín.
                </th>
                <th className="px-2 text-right">Fator lucro</th>
                <th className="px-2 text-right">Expect.</th>
                <th className="px-2 text-right">Queda máx.</th>
                <th className="px-2 text-right" title="Fora da amostra: período recente que não foi usado para escolher">
                  Recente
                </th>
                <th className="px-2 text-right">Real</th>
                <th className="px-3 text-right">Versão</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, limit).map((r) => {
                const m = r.metrics || {};
                const o = r.oos_metrics || {};
                const live = r.live || {};
                return (
                  <tr key={r.id} onClick={() => setDetail(r.id)} className="cursor-pointer border-b border-line/60 hover:bg-panel2">
                    <td className="px-3 py-2">
                      <b>{r.symbol}</b> <span className="text-muted">{r.timeframe}</span>
                      {r.horizon && (
                        <span className="block text-[10px] text-muted" title={r.avg_minutes != null ? `tempo médio em posição: ${num(r.avg_minutes, 0)} min` : undefined}>
                          {HORIZON_LABEL[r.horizon] ?? r.horizon}
                        </span>
                      )}
                    </td>
                    <td className="px-2">
                      {r.strategy_name} <span className="text-[10px] text-muted">({r.source})</span>
                    </td>
                    <td className="px-2">
                      <Badge tone={STATUS[r.status]?.tone ?? "slate"}>{STATUS[r.status]?.label ?? r.status}</Badge>
                    </td>
                    <td className="px-2 text-right tabular-nums">{m.trades}</td>
                    <td className="px-2 text-right tabular-nums">{pct(m.win_rate)}</td>
                    <td className="px-2 text-right tabular-nums text-gold">{pct(m.wilson_lb)}</td>
                    <td className="px-2 text-right tabular-nums">{num(m.profit_factor)}</td>
                    <td className={clsx("px-2 text-right tabular-nums", (m.expectancy_r ?? 0) >= 0 ? "text-up" : "text-down")}>{signed(m.expectancy_r)}R</td>
                    <td className="px-2 text-right tabular-nums text-muted">{num(m.max_dd_pct, 1)}%</td>
                    <td className={clsx("px-2 text-right tabular-nums", (o.expectancy_r ?? 0) >= 0 ? "text-up" : "text-down")}>
                      {pct(o.win_rate)} · {o.trades ?? 0}
                    </td>
                    <td className="px-2 text-right tabular-nums text-muted">{live.n ? `${live.wins}/${live.n}` : "—"}</td>
                    <td className="px-3 text-right text-muted">v{r.version}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="flex items-center justify-between px-4 py-3 text-xs text-muted">
            <span>
              {approved} aprovadas de {rows.length} testes (ativo × tempo gráfico × estratégia)
            </span>
            {rows.length > limit && (
              <Button variant="ghost" className="text-xs" onClick={() => setLimit(limit + 60)}>
                Mostrar mais
              </Button>
            )}
          </div>
        </div>
      )}
      <ProfileDetail id={detail} onClose={() => setDetail(null)} />
    </Card>
  );
}

function ProfileDetail({ id, onClose }: { id: number | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["profile", id], queryFn: () => api.get<any>(`/api/strategies/profiles/${id}`), enabled: !!id });
  if (!id) return null;
  const d = q.data;
  return (
    <div className="border-t border-line bg-panel2/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-pixel text-[10px] text-gold">{d ? `${d.strategy_name} · ${d.symbol} ${d.timeframe} · v${d.version}` : "Carregando…"}</h3>
        <button onClick={onClose} className="text-xs text-muted hover:text-white">
          fechar
        </button>
      </div>
      {d && (
        <div className="grid gap-4 text-sm md:grid-cols-3">
          <div>
            <h4 className="mb-1 text-xs font-semibold text-muted">Parâmetros</h4>
            <pre className="rounded bg-ink p-2 text-xs">{JSON.stringify(d.params, null, 1)}</pre>
            <h4 className="mb-1 mt-2 text-xs font-semibold text-muted">Filtros</h4>
            <pre className="rounded bg-ink p-2 text-xs">{Object.keys(d.filters || {}).length ? JSON.stringify(d.filters, null, 1) : "nenhum"}</pre>
            <h4 className="mb-1 mt-2 text-xs font-semibold text-muted">Risco</h4>
            <p className="text-xs">
              stop {num(d.risk?.sl_atr)} ATR · alvo {num(d.risk?.tp_r)}R
            </p>
          </div>
          <div>
            <h4 className="mb-1 text-xs font-semibold text-muted">Por que {d.status === "aprovada" ? "foi aprovada" : "não foi aprovada"}</h4>
            {(d.metrics?.reasons || []).length === 0 ? <p className="text-xs text-up">Passou em todos os critérios.</p> : d.metrics.reasons.map((r: string) => <p key={r} className="text-xs text-down">• {r}</p>)}
            <p className="mt-2 text-xs text-muted">
              Testada {ago(d.tested_at)} com {SOURCE_LABEL[d.data_source] ?? "dados do mercado"}. {d.evolved_at ? `Última evolução ${ago(d.evolved_at)}.` : ""}
            </p>
          </div>
          <div>
            <h4 className="mb-1 text-xs font-semibold text-muted">Histórico de testes</h4>
            <div className="scroll-thin max-h-48 overflow-y-auto text-xs">
              {(d.history || []).map((h: any) => (
                <div key={h.id} className="flex justify-between border-b border-line/50 py-1">
                  <span className="text-muted">
                    {dateTime(h.ts)} · {h.kind}
                  </span>
                  <span>
                    {pct(h.metrics?.win_rate)} · {signed(h.metrics?.expectancy_r)}R
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Backtest() {
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => api.get<any>("/api/strategies") });
  const [symbol, setSymbol] = useState("EURUSD");
  const [tf, setTf] = useState("H1");
  const [picked, setPicked] = useState<string[]>([]);
  const [result, setResult] = useState<any>(null);
  const [sel, setSel] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const symbols = useQuery({ queryKey: ["symbols", symbol], queryFn: () => api.get<any[]>(`/api/market/symbols?q=${encodeURIComponent(symbol)}`), enabled: symbol.length >= 2 });
  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<any>("/api/strategies/backtest", { symbol, timeframe: tf, strategies: picked.length ? picked : null });
      setResult(r);
      setSel(0);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };
  const cur = result?.results?.[sel];
  return (
    <div className="space-y-4">
      <Card title="Backtest sob demanda">
        <div className="grid gap-3 md:grid-cols-[1fr_140px_auto]">
          <Field label="Ativo (como aparece na sua corretora)">
            <Input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} list="symbol-list" />
            <datalist id="symbol-list">
              {symbols.data?.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.description}
                </option>
              ))}
            </datalist>
          </Field>
          <Field label="Tempo gráfico">
            <Select value={tf} onChange={setTf} options={TFS.map((t) => [t, t] as [string, string])} />
          </Field>
          <div className="flex items-end">
            <Button onClick={run} loading={busy} className="w-full">
              <FlaskConical className="h-4 w-4" /> Testar
            </Button>
          </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {catalog.data?.strategies.map((s: any) => {
            const on = picked.includes(s.key);
            return (
              <button key={s.key} onClick={() => setPicked(on ? picked.filter((k) => k !== s.key) : [...picked, s.key])} className={clsx("rounded-md border px-2 py-1 text-xs", on ? "border-gold bg-gold/15 text-gold" : "border-line text-slate-300 hover:bg-panel2")}>
                {s.name}
              </button>
            );
          })}
          <span className="self-center text-xs text-muted">{picked.length ? `${picked.length} escolhidas` : "nenhuma marcada = todas"}</span>
        </div>
        <div className="mt-3">
          <ErrorBox error={error} />
        </div>
      </Card>
      {result && (
        <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
          <Card title={`Resultados · ${result.symbol} ${result.timeframe} · ${result.bars} candles`} pad={false}>
            <p className="px-4 pt-3 text-xs text-muted">
              Período {dateTime(result.start)} a {dateTime(result.end)} ({SOURCE_LABEL[result.data_source] ?? "dados do mercado"}). “Recente” = depois de {dateTime(result.split_time)}, período que não entra na escolha.
            </p>
            <div className="scroll-thin overflow-x-auto">
              <table className="mt-2 w-full text-sm">
                <thead className="text-left text-[11px] uppercase text-muted">
                  <tr className="border-b border-line">
                    <th className="px-3 py-2">Estratégia</th>
                    <th className="px-2 text-right">Oper.</th>
                    <th className="px-2 text-right">Acerto</th>
                    <th className="px-2 text-right">Fator</th>
                    <th className="px-2 text-right">Expect.</th>
                    <th className="px-2 text-right">Resultado</th>
                    <th className="px-3 text-right">Recente</th>
                  </tr>
                </thead>
                <tbody>
                  {result.results.map((r: any, i: number) => (
                    <tr key={r.strategy} onClick={() => setSel(i)} className={clsx("cursor-pointer border-b border-line/60", sel === i ? "bg-panel2" : "hover:bg-panel2/60")}>
                      <td className="px-3 py-2">
                        {r.approved ? <Badge tone="green">ok</Badge> : <Badge tone="red">não</Badge>} {r.name}
                      </td>
                      <td className="px-2 text-right tabular-nums">{r.metrics.trades}</td>
                      <td className="px-2 text-right tabular-nums">{pct(r.metrics.win_rate)}</td>
                      <td className="px-2 text-right tabular-nums">{num(r.metrics.profit_factor)}</td>
                      <td className={clsx("px-2 text-right tabular-nums", r.metrics.expectancy_r >= 0 ? "text-up" : "text-down")}>{signed(r.metrics.expectancy_r)}R</td>
                      <td className={clsx("px-2 text-right tabular-nums", r.metrics.return_pct >= 0 ? "text-up" : "text-down")}>{signed(r.metrics.return_pct, 1)}%</td>
                      <td className="px-3 text-right tabular-nums text-muted">{pct(r.oos_metrics.win_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
          {cur && (
            <Card title={cur.name}>
              <p className="mb-2 text-xs text-muted">Patrimônio arriscando 1% por operação (base 100).</p>
              <LineChart points={cur.equity.map((p: any) => ({ t: p.t, v: p.equity }))} height={220} />
              {!cur.approved && (
                <div className="mt-2 text-xs text-down">
                  {cur.reasons.map((r: string) => (
                    <p key={r}>• {r}</p>
                  ))}
                </div>
              )}
              <div className="scroll-thin mt-3 max-h-64 overflow-y-auto text-xs">
                <table className="w-full">
                  <thead className="text-left text-muted">
                    <tr>
                      <th>Entrada</th>
                      <th>Lado</th>
                      <th>Saída</th>
                      <th className="text-right">R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cur.trades
                      .slice()
                      .reverse()
                      .map((t: any, i: number) => (
                        <tr key={i} className="border-t border-line/50">
                          <td className="py-1">{dateTime(t.entry_time)}</td>
                          <td>{t.side === "buy" ? "compra" : "venda"}</td>
                          <td>{EXIT_LABEL[t.reason] ?? t.reason}</td>
                          <td className={clsx("text-right tabular-nums", t.r >= 0 ? "text-up" : "text-down")}>{signed(t.r)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

function Catalog() {
  const q = useQuery({ queryKey: ["catalog"], queryFn: () => api.get<any>("/api/strategies") });
  if (q.isLoading) return <Loading />;
  const bySource: Record<string, any[]> = {};
  for (const s of q.data?.strategies || []) (bySource[s.source] ||= []).push(s);
  return (
    <div className="space-y-4">
      {Object.entries(bySource).map(([source, list]) => (
        <Card key={source} title={source}>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {list.map((s) => (
              <div key={s.key} className="rounded-lg border border-line bg-panel2 p-3">
                <div className="flex items-center justify-between gap-2">
                  <b className="text-sm">{s.name}</b>
                  <Badge tone="blue">{s.style}</Badge>
                </div>
                <p className="mt-1 text-xs text-slate-300">{s.description}</p>
                <p className="mt-1 text-xs text-muted">{s.how}</p>
                <div className="mt-2 flex flex-wrap gap-1">
                  {s.params.map((p: any) => (
                    <Badge key={p.name}>
                      {p.label}: {String(p.default)}
                    </Badge>
                  ))}
                  <Badge tone="gold">
                    stop {s.risk.sl_atr} ATR · alvo {s.risk.tp_r}R
                  </Badge>
                  {!s.enabled && <Badge tone="red">desligada</Badge>}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
      <Card title="Filtros que a Estela testa nas evoluções">
        <div className="grid gap-2 md:grid-cols-2">
          {q.data?.filters.map((f: any) => (
            <div key={f.key} className="text-sm">
              <b>{f.name}</b> <span className="text-muted">— {f.description}</span>
            </div>
          ))}
        </div>
        <p className="mt-3 text-xs text-muted">
          <Play className="mr-1 inline h-3 w-3" />A cada ciclo de evolução, ela varia um parâmetro por vez, sorteia combinações, liga/desliga esses filtros e ajusta stop e alvo. Escolhe com a parte antiga do histórico e só adota se melhorar também na parte recente, sem perder mais de 30% da expectativa.
        </p>
      </Card>
    </div>
  );
}
