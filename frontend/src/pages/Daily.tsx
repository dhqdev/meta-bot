import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CalendarClock, ClipboardList, Lightbulb, Play, Settings2, Target, Wrench } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router";
import { TranscriptLine, agentName } from "../components/Chat";
import { AGENT_COLORS, Avatar, Badge, Button, Card, Empty, ErrorBox, Loading, Stat } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, EXIT_LABEL, num, pct, signed } from "../lib/format";
import { onLiveEvent, useLive } from "../lib/live";

interface ReportSummary {
  id: number;
  day: string;
  ts: string | null;
  mode: string;
  pnl: number;
  trades: number;
  wins: number;
  status: "normal" | "meta" | "limite";
  mood: "bom" | "neutro" | "ruim";
  summary: string;
  ai: boolean;
  model: string;
  focus: string[];
  adjustments_count: number;
}

interface Report extends ReportSummary {
  transcript: Array<{ agent: string; text: string }>;
  sections: Array<{ agent: string; name: string; role: string; bullets: string[] }>;
  adjustments: Array<{ agent: string; kind: string; text: string }>;
  lessons: Array<{ agent: string; text: string }>;
  metrics: Record<string, any>;
}

interface Listing {
  reports: ReportSummary[];
  enabled: boolean;
  time: string;
  next_at: string;
  running: boolean;
  last_day: string | null;
}

const MOOD: Record<string, string> = { bom: "😄", neutro: "😐", ruim: "😟" };
const STATUS: Record<string, { label: string; tone: "green" | "red" | "slate" }> = {
  meta: { label: "🎯 meta batida", tone: "green" },
  limite: { label: "⛔ limite de perda", tone: "red" },
  normal: { label: "dia normal", tone: "slate" },
};
const ADJ_KIND: Record<string, string> = { horario: "horário", estrategia: "estratégia", horizonte: "scalper x posição", risco: "risco", saida: "saída" };
const HORIZON: Record<string, string> = { scalp: "Scalper", day: "Day trade", swing: "Posição longa" };

const dayLabel = (day: string, long = false) => {
  const text = new Date(`${day}T12:00:00`).toLocaleDateString("pt-BR", long ? { weekday: "long", day: "2-digit", month: "long" } : { weekday: "short", day: "2-digit", month: "2-digit" });
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export function DailyPage() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["daily"], queryFn: () => api.get<Listing>("/api/daily?limit=60"), refetchInterval: 60000 });
  const [day, setDay] = useState<string | undefined>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const reports = list.data?.reports ?? [];
  const current = day ?? reports[0]?.day;

  useEffect(
    () =>
      onLiveEvent((ev) => {
        if (ev.type === "daily") {
          void qc.invalidateQueries({ queryKey: ["daily"] });
          setDay(ev.day);
        }
      }),
    [qc],
  );

  const runNow = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<Report>("/api/daily/run");
      await qc.invalidateQueries({ queryKey: ["daily"] });
      setDay(r.day);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-pixel text-sm text-gold">DAILY</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Todo dia às <b className="text-slate-200">{list.data?.time ?? "19:00"}</b> (horário de Brasília) a equipe vai para a sala de reunião: cada agente conta como foi a sua parte, decidem juntos os <b className="text-slate-200">ajustes para amanhã</b> e a Aurora registra as lições. No dia seguinte eles já trabalham com esse aprendizado — a skill <i>Aprendizado da daily</i> sobe a cada reunião.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <Button onClick={runNow} loading={busy || list.data?.running}>
            <Play className="h-4 w-4" /> {list.data?.running ? "Reunião em andamento…" : "Fazer a daily agora"}
          </Button>
          <span className="flex items-center gap-1 text-[11px] text-muted">
            <CalendarClock className="h-3.5 w-3.5" />
            {list.data?.enabled === false ? (
              <>
                daily automática desligada ·{" "}
                <Link to="/config" className="text-sky hover:underline">
                  ligar
                </Link>
              </>
            ) : (
              <>próxima: {list.data ? dateTime(list.data.next_at) : "—"}</>
            )}
          </span>
        </div>
      </div>
      <ErrorBox error={error} />
      {list.isLoading ? (
        <Loading />
      ) : reports.length === 0 ? (
        <Empty>
          Ainda não houve daily. Ela acontece sozinha às {list.data?.time ?? "19:00"} quando o escritório trabalhou no dia — ou toque em <b>Fazer a daily agora</b> para ver como fica.
        </Empty>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
          <ReportList reports={reports} current={current} onPick={setDay} />
          {current && <ReportDetail day={current} />}
        </div>
      )}
    </div>
  );
}

function ReportList({ reports, current, onPick }: { reports: ReportSummary[]; current?: string; onPick: (d: string) => void }) {
  return (
    <div className="no-scrollbar -mx-3 flex gap-2 overflow-x-auto px-3 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0">
      {reports.map((r) => (
        <button
          key={r.day}
          onClick={() => onPick(r.day)}
          className={clsx(
            "w-44 shrink-0 rounded-xl border p-3 text-left transition lg:w-auto",
            current === r.day ? "border-gold bg-panel2" : "border-line bg-panel hover:bg-panel2",
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-semibold">{dayLabel(r.day)}</span>
            <span title={`humor: ${r.mood}`}>{MOOD[r.mood] ?? "😐"}</span>
          </div>
          <div className={clsx("mt-1 text-lg font-bold tabular-nums", r.pnl > 0 ? "text-up" : r.pnl < 0 ? "text-down" : "text-slate-300")}>{signed(r.pnl)}</div>
          <div className="mt-1 flex flex-wrap items-center gap-1 text-[11px] text-muted">
            <span>
              {r.trades} op. · {r.wins} ganhos
            </span>
            {r.status !== "normal" && <Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge>}
          </div>
        </button>
      ))}
    </div>
  );
}

function ReportDetail({ day }: { day: string }) {
  const live = useLive();
  const q = useQuery({ queryKey: ["daily", day], queryFn: () => api.get<Report>(`/api/daily/${day}`) });
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const r = q.data!;
  const m = r.metrics || {};
  const totals = m.totals || {};
  const cur = m.currency ? ` ${m.currency}` : "";
  const role = (id: string) => live.agents[id]?.role;
  return (
    <div className="min-w-0 space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <ClipboardList className="h-5 w-5 text-gold" />
          <h2 className="text-lg font-semibold">Daily · {dayLabel(r.day, true)}</h2>
          <Badge tone={STATUS[r.status]?.tone ?? "slate"}>{STATUS[r.status]?.label ?? r.status}</Badge>
          <Badge tone={r.mode === "live" ? "gold" : "blue"}>{r.mode === "live" ? "conta MT5" : "simulado"}</Badge>
          <Badge tone={r.ai ? "purple" : "slate"} className="ml-auto">
            {r.ai ? "falas pela IA" : "sem IA (regras)"}
          </Badge>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-slate-200">
          <span className="mr-1 text-lg">{MOOD[r.mood] ?? "😐"}</span>
          {r.summary}
        </p>
      </Card>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Resultado" value={`${signed(r.pnl)}${cur}`} tone={r.pnl > 0 ? "up" : r.pnl < 0 ? "down" : undefined} hint={totals.r != null ? `${signed(totals.r)}R no dia` : undefined} />
        <Stat label="Operações" value={r.trades} hint={`${r.wins} ganhos · ${totals.losses ?? r.trades - r.wins} perdas`} />
        <Stat label="Acerto" value={pct(totals.win_rate)} hint={m.signals ? `${m.signals.total} sinais no dia` : undefined} />
        <Stat label="Custo de IA" value={`US$ ${num(m.ai_cost_usd ?? 0, 3)}`} hint="no dia inteiro" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <Card title="A reunião">
          <div className="space-y-3">
            {r.transcript.map((line, i) => (
              <TranscriptLine key={i} agent={line.agent} text={line.text} role={role(line.agent)} />
            ))}
          </div>
        </Card>
        <div className="space-y-4">
          <Card title={<span className="flex items-center gap-1.5"><Target className="h-3.5 w-3.5" /> Foco de amanhã</span>}>
            {r.focus.length === 0 ? (
              <p className="text-sm text-muted">Sem foco especial: seguir o plano.</p>
            ) : (
              <ol className="space-y-2">
                {r.focus.map((f, i) => (
                  <li key={i} className="flex gap-2 text-sm">
                    <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-gold text-[11px] font-bold text-ink">{i + 1}</span>
                    <span>{f}</span>
                  </li>
                ))}
              </ol>
            )}
            <p className="mt-3 text-[11px] text-muted">O Gustavo lembra a equipe desse foco na manhã seguinte (aparece na conversa do escritório).</p>
          </Card>
          <Card title={<span className="flex items-center gap-1.5"><Wrench className="h-3.5 w-3.5" /> Ajustes para amanhã</span>}>
            {r.adjustments.length === 0 ? (
              <p className="text-sm text-muted">Nenhum ajuste necessário: o dia ficou dentro do esperado.</p>
            ) : (
              <div className="space-y-2.5">
                {r.adjustments.map((a, i) => (
                  <div key={i} className="flex gap-2 text-sm">
                    <Avatar id={a.agent} size={26} className="shrink-0" />
                    <div className="min-w-0">
                      <div className="text-[11px]">
                        <b style={{ color: AGENT_COLORS[a.agent] }}>{agentName(a.agent)}</b> <Badge tone="blue">{ADJ_KIND[a.kind] ?? a.kind}</Badge>
                      </div>
                      <p className="mt-0.5 leading-snug">{a.text}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="mt-3 flex items-start gap-1 text-[11px] text-muted">
              <Settings2 className="mt-0.5 h-3 w-3 shrink-0" /> Ajustes automáticos têm limite: nunca aumentam o risco, horários evitados valem só até o fim do dia seguinte.
            </p>
          </Card>
          <Card title={<span className="flex items-center gap-1.5"><Lightbulb className="h-3.5 w-3.5" /> Lições registradas</span>}>
            {r.lessons.length === 0 ? (
              <p className="text-sm text-muted">Nenhuma lição nova hoje.</p>
            ) : (
              <ul className="space-y-2">
                {r.lessons.map((l, i) => (
                  <li key={i} className="flex gap-2 text-sm">
                    <Avatar id={l.agent === "all" ? "auditor" : l.agent} size={22} className="shrink-0" />
                    <span>
                      <span className="text-[11px] text-muted">para {agentName(l.agent)}: </span>
                      {l.text}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <Link to="/agentes" className="mt-3 inline-block text-xs text-sky hover:underline">
              ver todas as lições da equipe →
            </Link>
          </Card>
        </div>
      </div>

      <HorizonCard today={m.by_horizon || []} horizons={m.horizons || []} currency={cur} />

      {r.sections.length > 0 && (
        <Card title="O que cada um viu">
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-4">
            {r.sections.map((s) => (
              <div key={s.agent} className="rounded-lg border border-line bg-panel2 p-3">
                <div className="flex items-center gap-2">
                  <Avatar id={s.agent} size={28} />
                  <div className="text-sm">
                    <b style={{ color: AGENT_COLORS[s.agent] }}>{s.name}</b> <span className="text-[11px] text-muted">{s.role}</span>
                  </div>
                </div>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-snug text-slate-300">
                  {s.bullets.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Numbers metrics={m} currency={cur} />
      {r.ai && r.model && <p className="text-right text-[11px] text-muted">falas geradas por {r.model}</p>}
    </div>
  );
}

function HorizonCard({ today, horizons, currency }: { today: any[]; horizons: any[]; currency: string }) {
  if (!today.length && !horizons.length) return null;
  const byKey = Object.fromEntries(today.map((h) => [h.key, h]));
  return (
    <Card title="Scalper x day trade x posição longa">
      <p className="mb-3 text-xs text-muted">
        Hoje e nos últimos 30 dias. A Estela testa as mesmas estratégias como scalper (alvo curto, sai rápido) e segurando mais tempo; o Gustavo passa a preferir o estilo que está dando mais resultado.
      </p>
      <div className="grid gap-3 md:grid-cols-3">
        {(horizons.length ? horizons : today.map((h) => ({ key: h.key, label: HORIZON[h.key] ?? h.key }))).map((h: any) => {
          const t = byKey[h.key];
          return (
            <div key={h.key} className="rounded-lg border border-line bg-panel2 p-3 text-sm">
              <div className="font-semibold">{h.label ?? HORIZON[h.key]}</div>
              {h.desc && <div className="text-[11px] text-muted">{h.desc}</div>}
              <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
                <span className="text-muted">hoje</span>
                <span className={clsx("text-right tabular-nums", (t?.pnl ?? 0) > 0 ? "text-up" : (t?.pnl ?? 0) < 0 ? "text-down" : "")}>
                  {t ? `${t.n} op. · ${signed(t.pnl)}${currency}` : "—"}
                </span>
                {h.live_trades != null && (
                  <>
                    <span className="text-muted">30 dias (real)</span>
                    <span className="text-right tabular-nums">
                      {h.live_trades} op. · {signed(h.live_r)}R
                    </span>
                    <span className="text-muted">backtest aprovados</span>
                    <span className="text-right tabular-nums">
                      {h.approved} · {signed(h.bt_oos_expectancy_r)}R/op
                    </span>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function Numbers({ metrics, currency }: { metrics: Record<string, any>; currency: string }) {
  const groups: Array<[string, any[], (k: string) => string]> = [
    ["Por estratégia", metrics.by_strategy || [], (k) => k],
    ["Por ativo", metrics.by_symbol || [], (k) => k],
    ["Por horário (Brasília)", metrics.by_hour_local || [], (k) => `${k}h`],
  ];
  const exits = Object.entries(metrics.exits || {}) as Array<[string, number]>;
  const vetoes: any[] = metrics.signals?.top_vetoes || [];
  if (!groups.some(([, rows]) => rows.length) && !exits.length && !vetoes.length) return null;
  return (
    <Card title="Números do dia">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {groups.map(([title, rows, fmt]) => (
          <div key={title}>
            <h4 className="mb-1.5 text-xs font-semibold text-slate-300">{title}</h4>
            {rows.length === 0 && <p className="text-xs text-muted">—</p>}
            {rows.slice(0, 6).map((g) => (
              <div key={g.key} className="flex justify-between gap-2 border-b border-line/50 py-1 text-xs">
                <span className="truncate">{fmt(g.key)}</span>
                <span className={clsx("tabular-nums", g.pnl > 0 ? "text-up" : g.pnl < 0 ? "text-down" : "")}>
                  {g.n} op · {signed(g.pnl)}
                  {currency}
                </span>
              </div>
            ))}
          </div>
        ))}
        <div>
          <h4 className="mb-1.5 text-xs font-semibold text-slate-300">Saídas e vetos</h4>
          {exits.map(([k, n]) => (
            <div key={k} className="flex justify-between border-b border-line/50 py-1 text-xs">
              <span>{EXIT_LABEL[k] ?? k}</span>
              <span className="tabular-nums">{n}</span>
            </div>
          ))}
          {vetoes.map((v) => (
            <div key={v.reason} className="flex justify-between gap-2 border-b border-line/50 py-1 text-xs text-muted">
              <span className="truncate" title={v.reason}>
                vetado — {v.reason}
              </span>
              <span className="tabular-nums">{v.n}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
