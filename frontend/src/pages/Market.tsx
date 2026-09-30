import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { CandleChart } from "../components/Charts";
import { Badge, Card, Empty, Loading, Select } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, money, num, signed, time } from "../lib/format";

const TFS: Array<[string, string]> = ["M5", "M15", "M30", "H1", "H4", "D1"].map((t) => [t, t]);

export function MarketPage() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: () => api.get<any>("/api/market/overview"), refetchInterval: 15000 });
  const [symbol, setSymbol] = useState<string>("");
  const [tf, setTf] = useState("H1");
  useEffect(() => {
    if (!symbol && overview.data?.symbols?.length) setSymbol(overview.data.symbols[0].symbol);
  }, [overview.data, symbol]);
  const candles = useQuery({ queryKey: ["candles", symbol, tf], queryFn: () => api.get<any>(`/api/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${tf}&count=300`), enabled: !!symbol, refetchInterval: 30000 });
  const hours = useQuery({ queryKey: ["hours", symbol], queryFn: () => api.get<any>(`/api/market/hours?symbol=${encodeURIComponent(symbol)}`), enabled: !!symbol });
  const news = useQuery({ queryKey: ["news"], queryFn: () => api.get<any[]>("/api/news?limit=80"), refetchInterval: 60000 });
  const calendar = useQuery({ queryKey: ["calendar"], queryFn: () => api.get<any[]>("/api/calendar?days=7"), refetchInterval: 300000 });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-pixel text-sm text-gold">MERCADO</h1>
        <p className="mt-1 text-sm text-muted">
          Sessões abertas agora: {overview.data?.sessions?.join(", ") || "nenhuma"} · dados {overview.data?.source === "mt5" ? "do MetaTrader 5" : "do mercado simulado"}
        </p>
      </div>
      <Card title="Ativos acompanhados" pad={false}>
        {overview.isLoading ? (
          <Loading />
        ) : (
          <div className="scroll-thin overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase text-muted">
                <tr className="border-b border-line">
                  <th className="px-3 py-2">Ativo</th>
                  <th className="px-2 text-right">Preço</th>
                  <th className="px-2 text-right">Dia</th>
                  <th className="px-2 text-right">Notícias (Nina)</th>
                  <th className="px-2 text-right">Hora agora (Hugo)</th>
                  <th className="px-2">Melhores horas (Brasília)</th>
                  <th className="px-3">Pausa</th>
                </tr>
              </thead>
              <tbody>
                {overview.data?.symbols?.map((s: any) => (
                  <tr key={s.symbol} onClick={() => setSymbol(s.symbol)} className={clsx("cursor-pointer border-b border-line/60", symbol === s.symbol ? "bg-panel2" : "hover:bg-panel2/60")}>
                    <td className="px-3 py-2 font-semibold">{s.symbol}</td>
                    <td className="px-2 text-right tabular-nums">{s.bid != null ? num(s.bid, 5) : <span className="text-down">{s.error}</span>}</td>
                    <td className={clsx("px-2 text-right tabular-nums", (s.change_pct ?? 0) >= 0 ? "text-up" : "text-down")}>{s.change_pct != null ? `${signed(s.change_pct)}%` : "—"}</td>
                    <td className="px-2 text-right">
                      <NewsScore score={s.news?.score} conf={s.news?.confidence} count={s.news?.count} />
                    </td>
                    <td className="px-2 text-right tabular-nums">{Math.round((s.hour_quality ?? 0) * 100)}/100</td>
                    <td className="px-2 text-xs text-muted">{(s.best_hours_local || []).map((h: number) => `${h}h`).join(" ") || "—"}</td>
                    <td className="px-3">{s.blackout ? <Badge tone="red">{s.blackout.title}</Badge> : <span className="text-muted">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Card title={`${symbol} · gráfico`} actions={<Select value={tf} onChange={setTf} options={TFS} className="w-24 py-1" />}>
          {candles.data ? <CandleChart candles={candles.data.candles} height={340} /> : <Loading />}
        </Card>
        <Card title={`${symbol} · qualidade por hora (Hugo)`}>
          <HourMap data={hours.data} />
        </Card>
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
        <Card title="Notícias (Nina)" pad={false}>
          <div className="scroll-thin max-h-[480px] divide-y divide-line overflow-y-auto">
            {!news.data?.length && (
              <div className="p-4">
                <Empty>Sem notícias ainda. A Nina lê os feeds a cada 10 minutos com o escritório ligado.</Empty>
              </div>
            )}
            {news.data?.map((n) => (
              <a key={n.id} href={n.url} target="_blank" rel="noreferrer noopener" className="block px-4 py-2.5 hover:bg-panel2">
                <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted">
                  <Badge tone={n.impact === "high" ? "red" : n.impact === "medium" ? "gold" : "slate"}>{n.impact === "high" ? "alto impacto" : n.impact === "medium" ? "médio" : "baixo"}</Badge>
                  <span>{n.source}</span>
                  <span>· {dateTime(n.published_at)}</span>
                  {n.ai && <Badge tone="green">IA</Badge>}
                  {Object.entries(n.symbols || {}).map(([sym, sc]: any) => (
                    <Badge key={sym} tone={sc > 0.1 ? "green" : sc < -0.1 ? "red" : "slate"}>
                      {sym} {signed(sc, 1)}
                    </Badge>
                  ))}
                </div>
                <div className="mt-1 text-sm">{n.title}</div>
                {n.summary_pt && <div className="mt-0.5 text-xs text-slate-400">{n.summary_pt}</div>}
              </a>
            ))}
          </div>
        </Card>
        <Card title="Calendário econômico (Hugo)" pad={false}>
          <div className="scroll-thin max-h-[480px] divide-y divide-line overflow-y-auto">
            {!calendar.data?.length && (
              <div className="p-4">
                <Empty>Sem eventos carregados (o Hugo busca o calendário da ForexFactory a cada hora).</Empty>
              </div>
            )}
            {calendar.data?.map((e) => (
              <div key={e.id} className="flex items-center gap-2 px-4 py-2 text-sm">
                <span className="w-24 shrink-0 text-xs text-muted">
                  {new Date(e.ts).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", timeZone: "America/Sao_Paulo" })} {time(e.ts)}
                </span>
                <Badge tone={e.impact === "High" ? "red" : e.impact === "Medium" ? "gold" : "slate"}>{e.currency}</Badge>
                <span className="flex-1">{e.title}</span>
                <span className="text-xs text-muted">{e.actual ? `atual ${e.actual}` : e.forecast ? `prev. ${e.forecast}` : ""}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function NewsScore({ score, conf, count }: { score?: number; conf?: number; count?: number }) {
  if (!count) return <span className="text-muted">—</span>;
  const s = score ?? 0;
  return (
    <span className={clsx("tabular-nums", s > 0.1 ? "text-up" : s < -0.1 ? "text-down" : "text-slate-300")} title={`confiança ${money((conf ?? 0) * 100, 0)}% · ${count} notícias`}>
      {signed(s)} <span className="text-[10px] text-muted">({count})</span>
    </span>
  );
}

function HourMap({ data }: { data?: any }) {
  if (!data?.hours?.length) return <Empty>O mapa aparece depois que o Hugo analisa o histórico (ao ligar o escritório).</Empty>;
  const rows = [...data.hours].sort((a: any, b: any) => a.hour_local - b.hour_local);
  return (
    <div>
      <div className="grid grid-cols-6 gap-1.5 sm:grid-cols-8 md:grid-cols-12">
        {rows.map((h: any) => {
          const q = h.quality ?? 0;
          const bg = h.n < 10 ? "#1f2940" : `rgba(46, 204, 113, ${0.12 + q * 0.75})`;
          return (
            <div key={h.hour_utc} className="rounded-md p-1.5 text-center" style={{ background: bg }} title={`amplitude ${num(h.range_pct, 3)}% · movimento/custo ${num(h.edge, 1)}× · direção ${num(h.efficiency * 100, 0)}%${h.live_trades ? ` · ${h.live_trades} operações reais` : ""}`}>
              <div className="text-[11px] font-semibold">{h.hour_local}h</div>
              <div className="text-[10px] text-slate-200">{h.n < 10 ? "fechado" : Math.round(q * 100)}</div>
            </div>
          );
        })}
      </div>
      <p className="mt-3 text-xs text-muted">
        Nota de 0 a 100: quanto o preço anda naquela hora comparado ao custo de operar (spread) e quanto anda numa direção só, misturado com o resultado real das operações naquela hora. Horários de Brasília.
      </p>
    </div>
  );
}
