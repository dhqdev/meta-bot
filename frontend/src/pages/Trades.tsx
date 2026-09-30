import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useState } from "react";
import { LineChart } from "../components/Charts";
import { Badge, Button, Card, Empty, Loading, Select, Stat } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, DIRECTION_LABEL, EXIT_LABEL, money, num, pct, signed } from "../lib/format";
import { useLive } from "../lib/live";

const SIGNAL_TONE: Record<string, "green" | "red" | "gold" | "slate" | "blue"> = { executado: "green", vetado: "red", falhou: "red", aguardando: "gold", expirado: "slate", aprovado: "blue", proposto: "slate", cancelado: "slate" };

export function TradesPage() {
  const live = useLive();
  const [mode, setMode] = useState<string>(live.system.mode || "paper");
  const summary = useQuery({ queryKey: ["summary", mode], queryFn: () => api.get<any>(`/api/trades/summary?mode=${mode}&days=180`), refetchInterval: 30000 });
  const open = useQuery({ queryKey: ["open-trades"], queryFn: () => api.get<any[]>("/api/trades/open"), refetchInterval: 8000 });
  const trades = useQuery({ queryKey: ["trades", mode], queryFn: () => api.get<any[]>(`/api/trades?status=closed&mode=${mode}&limit=300`), refetchInterval: 30000 });
  const signals = useQuery({ queryKey: ["signals"], queryFn: () => api.get<any[]>("/api/strategies/signals?limit=60"), refetchInterval: 20000 });
  const decisions = useQuery({ queryKey: ["decisions"], queryFn: () => api.get<any[]>("/api/decisions?limit=15"), refetchInterval: 60000 });
  const s = summary.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-pixel text-sm text-gold">OPERAÇÕES</h1>
          <p className="mt-1 text-sm text-muted">Tudo o que o Caio executou, o que a Rita vetou e as decisões do Gustavo.</p>
        </div>
        <Select value={mode} onChange={setMode} options={[["paper", "Conta simulada"], ["live", "Conta da corretora (MT5)"]]} className="w-56" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-6">
        <Stat label="Resultado" value={signed(s?.pnl)} tone={(s?.pnl ?? 0) >= 0 ? "up" : "down"} hint="últimos 180 dias" />
        <Stat label="Hoje" value={signed(s?.today_pnl)} tone={(s?.today_pnl ?? 0) >= 0 ? "up" : "down"} />
        <Stat label="Operações" value={s?.trades ?? 0} />
        <Stat label="Acerto" value={pct(s?.win_rate)} />
        <Stat label="Fator de lucro" value={num(s?.profit_factor)} />
        <Stat label="Média por operação" value={`${signed(s?.avg_r)}R`} />
      </div>
      <Card title="Patrimônio">
        {s?.equity?.length > 1 ? <LineChart points={s.equity.map((p: any) => ({ t: p.t, v: p.equity }))} height={240} /> : <Empty>O Caio registra o patrimônio a cada minuto; a curva aparece aqui.</Empty>}
      </Card>
      <Card title={`Posições abertas (${open.data?.length ?? 0})`} pad={false}>
        {open.isLoading ? (
          <Loading />
        ) : !open.data?.length ? (
          <div className="p-4">
            <Empty>Nenhuma posição aberta.</Empty>
          </div>
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase text-muted">
                <tr className="border-b border-line">
                  <th className="px-3 py-2">Ativo</th>
                  <th className="px-2">Lado</th>
                  <th className="px-2 text-right">Lote</th>
                  <th className="px-2 text-right">Entrada</th>
                  <th className="px-2 text-right">Atual</th>
                  <th className="px-2 text-right">Stop</th>
                  <th className="px-2 text-right">Alvo</th>
                  <th className="px-2 text-right">Resultado</th>
                  <th className="px-3" />
                </tr>
              </thead>
              <tbody>
                {open.data.map((t) => (
                  <tr key={t.id} className="border-b border-line/60">
                    <td className="px-3 py-2">
                      <b>{t.symbol}</b> <span className="text-xs text-muted">{t.strategy_name} · {t.timeframe} · {t.mode === "live" ? "MT5" : "sim."}</span>
                    </td>
                    <td className="px-2">
                      <Badge tone={t.direction === "buy" ? "green" : "red"}>{t.direction === "buy" ? "compra" : "venda"}</Badge>
                      {t.mgmt?.be && <Badge tone="blue">0×0</Badge>}
                      {t.mgmt?.trailing && <Badge tone="purple">trailing</Badge>}
                    </td>
                    <td className="px-2 text-right tabular-nums">{t.volume}</td>
                    <td className="px-2 text-right tabular-nums">{num(t.entry_price, 5)}</td>
                    <td className="px-2 text-right tabular-nums">{num(t.price, 5)}</td>
                    <td className="px-2 text-right tabular-nums text-down">{num(t.sl, 5)}</td>
                    <td className="px-2 text-right tabular-nums text-up">{num(t.tp, 5)}</td>
                    <td className={clsx("px-2 text-right tabular-nums font-semibold", (t.open_pnl ?? 0) >= 0 ? "text-up" : "text-down")}>
                      {signed(t.open_pnl)} <span className="text-xs">({signed(t.open_r)}R)</span>
                    </td>
                    <td className="px-3 text-right">
                      <Button
                        variant="ghost"
                        className="px-2 py-1 text-xs"
                        onClick={async () => {
                          if (confirm(`Fechar ${t.symbol} agora a mercado?`)) {
                            await api.post(`/api/trades/${t.id}/close`);
                            void open.refetch();
                          }
                        }}
                      >
                        Fechar
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Resultado por estratégia" pad={false}>
          <table className="w-full text-sm">
            <tbody>
              {s?.by_strategy?.map((r: any) => (
                <tr key={r.strategy} className="border-b border-line/60">
                  <td className="px-4 py-2">{r.name}</td>
                  <td className="px-2 text-right text-muted">{r.trades} oper.</td>
                  <td className="px-2 text-right">{pct(r.wins / Math.max(1, r.trades))}</td>
                  <td className={clsx("px-4 text-right tabular-nums", r.pnl >= 0 ? "text-up" : "text-down")}>{signed(r.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!s?.by_strategy?.length && (
            <div className="p-4">
              <Empty>Sem operações fechadas ainda.</Empty>
            </div>
          )}
        </Card>
        <Card title="Decisões do Gerente" pad={false}>
          <div className="scroll-thin max-h-80 divide-y divide-line overflow-y-auto">
            {decisions.data?.map((d) => (
              <div key={d.id} className="px-4 py-2 text-sm">
                <div className="flex items-center gap-2 text-xs text-muted">
                  {dateTime(d.ts)} {d.ai ? <Badge tone="green" className="max-w-[16rem] truncate">IA{d.model ? ` · ${String(d.model).replace(/^openrouter:/, "")}` : ""}</Badge> : <Badge>pontuação</Badge>}
                </div>
                <div className="mt-1">{d.plan.length ? d.plan.map((p: any) => `${p.symbol} ${p.timeframe} ${p.strategy_name} (${DIRECTION_LABEL[p.direction] ?? p.direction})`).join(" · ") : "Ficar de fora"}</div>
                {d.rationale && <div className="mt-0.5 text-xs text-slate-400">{d.rationale}</div>}
              </div>
            ))}
            {!decisions.data?.length && (
              <div className="p-4">
                <Empty>O Gustavo decide a cada 15 minutos com o escritório ligado.</Empty>
              </div>
            )}
          </div>
        </Card>
      </div>
      <Card title="Sinais (Estela → Gustavo → Rita → Caio)" pad={false}>
        <div className="scroll-thin max-h-96 overflow-auto">
          <table className="w-full text-sm">
            <tbody>
              {signals.data?.map((g) => (
                <tr key={g.id} className="border-b border-line/60">
                  <td className="px-4 py-2 text-xs text-muted">{dateTime(g.ts)}</td>
                  <td className="px-2">
                    <b>{g.symbol}</b> {g.timeframe}
                  </td>
                  <td className="px-2">{g.strategy_name}</td>
                  <td className="px-2">{g.direction === "buy" ? "compra" : "venda"}{g.entry_type === "stop" ? " (stop)" : ""}</td>
                  <td className="px-2">
                    <Badge tone={SIGNAL_TONE[g.status] ?? "slate"}>{g.status}</Badge>
                  </td>
                  <td className="px-4 text-xs text-muted">{g.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!signals.data?.length && (
            <div className="p-4">
              <Empty>Nenhum sinal nos últimos 14 dias.</Empty>
            </div>
          )}
        </div>
      </Card>
      <Card title="Histórico" pad={false}>
        <div className="scroll-thin max-h-[480px] overflow-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-panel text-left text-[11px] uppercase text-muted">
              <tr className="border-b border-line">
                <th className="px-3 py-2">Saída</th>
                <th className="px-2">Ativo</th>
                <th className="px-2">Estratégia</th>
                <th className="px-2">Lado</th>
                <th className="px-2">Motivo</th>
                <th className="px-2 text-right">Lote</th>
                <th className="px-2 text-right">R</th>
                <th className="px-3 text-right">Resultado</th>
              </tr>
            </thead>
            <tbody>
              {trades.data?.map((t) => (
                <tr key={t.id} className="border-b border-line/60">
                  <td className="px-3 py-1.5 text-xs text-muted">{dateTime(t.exit_time)}</td>
                  <td className="px-2 font-semibold">{t.symbol}</td>
                  <td className="px-2 text-xs">{t.strategy_name} · {t.timeframe}</td>
                  <td className="px-2">{t.direction === "buy" ? "compra" : "venda"}</td>
                  <td className="px-2 text-xs">{EXIT_LABEL[t.exit_reason] ?? t.exit_reason}</td>
                  <td className="px-2 text-right tabular-nums">{t.volume}</td>
                  <td className={clsx("px-2 text-right tabular-nums", t.pnl_r >= 0 ? "text-up" : "text-down")}>{signed(t.pnl_r)}</td>
                  <td className={clsx("px-3 text-right tabular-nums font-semibold", t.pnl >= 0 ? "text-up" : "text-down")}>{money(t.pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!trades.data?.length && (
            <div className="p-4">
              <Empty>Sem operações fechadas neste modo.</Empty>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
