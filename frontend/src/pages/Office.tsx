import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ClipboardList, MessagesSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { MessageRow, TEAM_ORDER, TeamChat } from "../components/Chat";
import { AGENT_COLORS, AGENT_NAMES, Avatar, Badge, Bar, Card, Stat } from "../components/ui";
import { api } from "../lib/api";
import { cash, dateTime, DIRECTION_LABEL, pct, signed, signedCash, time } from "../lib/format";
import { onLiveEvent, useLive } from "../lib/live";
import { OfficeCanvas } from "../office/OfficeCanvas";

interface Summary {
  win_rate: number;
  trades: number;
  today_pnl: number;
  pnl: number;
}

const HORIZON: Record<string, string> = { scalp: "scalper", day: "day trade", swing: "posição longa" };
const STATE_LABEL: Record<string, string> = { working: "trabalhando", idle: "disponível", alert: "em alerta", error: "com erro", meeting: "em reunião", off: "fora" };
type Tab = "chat" | "feed" | "plan" | "agent";
const TABS: Array<[Tab, string]> = [
  ["chat", "Conversa"],
  ["feed", "Atividade"],
  ["plan", "Plano"],
  ["agent", "Agente"],
];

const REVIEW_LABEL: Record<string, string> = { hold: "mantém", adjust: "ajustou", close: "fechou" };

/** Última revisão do Gustavo nesta posição (de hora em hora). */
function ReviewNote({ review }: { review: { at: string; action: string; text: string } }) {
  return (
    <p className="w-full text-[11px] leading-snug text-muted">
      🔍 Gustavo às {time(review.at)}: <b className="text-slate-300">{REVIEW_LABEL[review.action] ?? review.action}</b> — {review.text}
    </p>
  );
}

export function OfficePage() {
  const live = useLive();
  const [selected, setSelected] = useState<string | undefined>();
  const [tab, setTab] = useState<Tab>("chat");
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api.get<Summary>("/api/trades/summary?days=30"), refetchInterval: 30000 });
  const open = useQuery({ queryKey: ["open-trades"], queryFn: () => api.get<any[]>("/api/trades/open"), refetchInterval: 10000 });
  const acc = live.office.account || {};
  const risk = live.office.risk || {};
  const today = risk.day_pnl ?? summary.data?.today_pnl;

  const pick = (id?: string) => {
    setSelected(id);
    if (id) setTab("agent");
  };

  return (
    <div className="space-y-3">
      {!live.system.running && live.office.office_break?.until && (
        <div className="rounded-xl border border-indigo-400/30 bg-indigo-500/10 px-4 py-3 text-sm text-indigo-100">
          🌙 <b>Pausa depois da daily:</b> a equipe volta às <b>{time(live.office.office_break.until)}</b>. Ninguém abre posição nova agora; o Caio continua protegendo as posições abertas.
        </div>
      )}
      {!live.system.running && !live.office.office_break?.until && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
          O escritório está <b>fechado</b>: a equipe está no lounge. Toque em <b>Ligar escritório</b> (botão no topo) para os agentes começarem a trabalhar — tudo começa no modo simulado.
        </div>
      )}
      <TeamStrip selected={selected} onPick={(id) => pick(selected === id ? undefined : id)} />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
        <div className="min-w-0 space-y-3">
          <OfficeCanvas selected={selected} onSelect={pick} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Patrimônio" value={cash(acc.equity, acc.currency)} hint={live.system.mode === "live" ? "conta MT5" : "conta simulada"} tone="gold" />
            <Stat label="Hoje" value={signedCash(today, acc.currency || risk.currency)} hint={risk.day_pct != null ? `${signed(risk.day_pct)}% no dia` : undefined} tone={(today ?? 0) >= 0 ? "up" : "down"} />
            <Stat label="Acerto (30 dias)" value={pct(summary.data?.win_rate)} hint={`${summary.data?.trades ?? 0} operações`} />
            <Stat label="Posições abertas" value={open.data?.length ?? 0} hint={`limite ${risk.max_positions ?? "—"}`} />
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <DayGoals />
            <DailyTeaser />
          </div>
          <MarketToday />
          {open.data && open.data.length > 0 && (
            <Card title="Posições abertas" pad={false}>
              <div className="divide-y divide-line">
                {open.data.map((t) => (
                  <div key={t.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
                    <Badge tone={t.direction === "buy" ? "green" : "red"}>{t.direction === "buy" ? "COMPRA" : "VENDA"}</Badge>
                    <b>{t.symbol}</b>
                    <span className="text-muted">
                      {t.strategy_name} · {t.timeframe}
                    </span>
                    <span className="ml-auto tabular-nums">{t.volume} lote(s)</span>
                    <span className={clsx("font-semibold tabular-nums", (t.open_pnl ?? 0) >= 0 ? "text-up" : "text-down")}>
                      {signed(t.open_pnl)} ({signed(t.open_r)}R)
                    </span>
                    {t.mgmt?.last_review && <ReviewNote review={t.mgmt.last_review} />}
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
        <Card
          className="h-[560px] min-w-0 xl:sticky xl:top-24 xl:h-[calc(100vh-120px)]"
          fill
          pad={false}
          title={
            <div className="flex gap-1">
              {TABS.map(([k, label]) => (
                <button
                  key={k}
                  onClick={() => setTab(k)}
                  className={clsx("rounded-md px-2.5 py-1 font-sans text-xs font-semibold normal-case tracking-normal", tab === k ? "bg-panel2 text-gold" : "text-muted hover:text-slate-200")}
                >
                  {label}
                </button>
              ))}
            </div>
          }
        >
          {tab === "chat" && <TeamChat messages={live.messages} />}
          {tab === "feed" && <Feed />}
          {tab === "plan" && <PlanPanel />}
          {tab === "agent" && <AgentPanel id={selected} onPick={pick} />}
        </Card>
      </div>
    </div>
  );
}

/** Por que a equipe está (ou não) operando: horário do câmbio, plano, sinais e vetos de hoje. */
function MarketToday() {
  const q = useQuery({ queryKey: ["diagnostico"], queryFn: () => api.get<any>("/api/system/diagnostico"), refetchInterval: 60000 });
  const d = q.data;
  if (!d) return null;
  const tone: Record<string, string> = {
    stop: "border-red-500/30 bg-red-500/10 text-red-100",
    warn: "border-amber-500/30 bg-amber-500/10 text-amber-100",
    info: "border-sky-500/30 bg-sky-500/10 text-sky-100",
    ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-100",
  };
  const f = d.funnel;
  const steps: Array<[string, number]> = [
    ["estratégias aprovadas", f.approved],
    ["candidatos livres", f.candidates - f.blocked],
    ["no plano agora", f.plan],
    ["sinais hoje", f.signals],
    ["operações hoje", f.trades],
  ];
  return (
    <Card
      title="Mercado e operações hoje"
      actions={<Badge tone={d.fx.open ? "green" : "red"}>{d.fx.open ? `câmbio aberto · fecha ${d.fx.next_change_local ?? ""}` : `câmbio fechado · abre ${d.fx.next_change_local ?? ""}`}</Badge>}
    >
      <div className="space-y-1.5">
        {d.reasons.map((r: any, i: number) => (
          <div key={i} className={clsx("rounded-lg border px-3 py-2 text-sm", tone[r.level] ?? tone.info)}>
            {r.text}
          </div>
        ))}
      </div>
      <div className="mt-3 grid grid-cols-5 gap-1 text-center">
        {steps.map(([label, n]) => (
          <div key={label} className="rounded-md border border-line bg-ink/30 px-1 py-1.5">
            <div className="text-base font-semibold tabular-nums text-slate-100">{n}</div>
            <div className="text-[10px] leading-tight text-muted">{label}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {d.symbols.map((x: any) => (
          <span key={x.symbol} title={x.open === false ? "fechado agora" : x.open ? "aberto" : "sem cotação"} className="flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-slate-300">
            <span className={clsx("h-1.5 w-1.5 rounded-full", x.open ? "bg-emerald-400" : x.open === false ? "bg-red-400" : "bg-slate-500")} />
            {x.symbol}
            <span className="text-muted">· {x.approved}</span>
          </span>
        ))}
      </div>
      <p className="mt-2 text-[11px] leading-snug text-muted">
        Câmbio: {d.schedule.fx_hours}. {d.schedule.weekend_close ? `Posições fechadas ${d.schedule.weekend_close}. ` : ""}
        {d.schedule.daily ? `Daily às ${d.schedule.daily}${d.schedule.daily_break_minutes ? `, com ${d.schedule.daily_break_minutes} min de pausa` : ""}. ` : ""}
        O número ao lado de cada par é quantas estratégias aprovadas ele tem.
      </p>
    </Card>
  );
}

/** Faixa com a equipe: quem está fazendo o quê agora (toque para ver o agente). */
function TeamStrip({ selected, onPick }: { selected?: string; onPick: (id: string) => void }) {
  const { agents } = useLive();
  return (
    <div className="no-scrollbar -mx-3 flex gap-2 overflow-x-auto px-3 md:mx-0 md:px-0">
      {TEAM_ORDER.filter((id) => agents[id]).map((id) => {
        const a = agents[id];
        const ring = a.state === "alert" || a.state === "error" ? "ring-down" : a.state === "working" ? "ring-gold" : a.state === "meeting" ? "ring-sky" : "ring-line";
        return (
          <button
            key={id}
            onClick={() => onPick(id)}
            className={clsx(
              "flex w-44 shrink-0 items-center gap-2 rounded-xl border px-2 py-1.5 text-left transition md:w-auto md:min-w-0 md:flex-1",
              selected === id ? "border-gold bg-panel2" : "border-line bg-panel hover:bg-panel2",
            )}
            title={a.task}
          >
            <span className={clsx("relative shrink-0 rounded-lg ring-2", ring)}>
              <Avatar id={id} size={30} />
              {a.status_emoji && <span className="absolute -right-1.5 -top-1.5 text-[11px]">{a.status_emoji}</span>}
            </span>
            <span className="min-w-0">
              <span className="block text-xs font-semibold" style={{ color: AGENT_COLORS[id] }}>
                {a.name}
              </span>
              <span className="block truncate text-[10px] leading-tight text-muted">{a.task || a.role}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Meta de ganho e limite de perda do dia (a equipe para quando bate um dos dois). */
function DayGoals() {
  const risk = useLive().office.risk || {};
  const cur = risk.currency;
  const pnl = Number(risk.day_pnl ?? 0);
  const loss = Number(risk.daily_loss_money ?? 0);
  const target = Number(risk.daily_target_money ?? 0);
  const stop = risk.day_stop as string | null | undefined;
  return (
    <Card
      title="Metas do dia"
      actions={
        <Link to="/config" className="text-[11px] text-sky hover:underline">
          ajustar
        </Link>
      }
    >
      {stop === "target" && (
        <div className="mb-3 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-sm text-emerald-200">
          🎯 Meta batida ({signedCash(risk.day_stop_pnl ?? pnl, cur)}). A equipe parou até amanhã.
        </div>
      )}
      {stop === "loss" && (
        <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">
          ⛔ Limite de perda atingido ({signedCash(risk.day_stop_pnl ?? pnl, cur)}). A equipe parou até amanhã.
        </div>
      )}
      {risk.kill_switch && <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-200">🚨 Trava geral ativa: {risk.kill_reason}. Libere em Config.</div>}
      <div className="space-y-3 text-sm">
        <div>
          <div className="mb-1 flex justify-between gap-2 text-xs">
            <span className="text-slate-300">Meta de ganho</span>
            <span className="tabular-nums text-muted">{target > 0 ? `${signedCash(Math.max(0, pnl), cur)} de ${signedCash(target, cur)}` : "sem meta (opera o dia todo)"}</span>
          </div>
          <Bar value={target > 0 ? Math.max(0, pnl) / target : 0} tone="green" />
        </div>
        <div>
          <div className="mb-1 flex justify-between gap-2 text-xs">
            <span className="text-slate-300">Limite de perda</span>
            <span className="tabular-nums text-muted">{loss > 0 ? `${pnl < 0 ? "−" : ""}${cash(Math.max(0, -pnl), cur)} de −${cash(loss, cur)}` : "sem limite diário"}</span>
          </div>
          <Bar value={loss > 0 ? Math.max(0, -pnl) / loss : 0} tone="red" />
        </div>
        {risk.adaptive_mult != null && risk.adaptive_mult < 1 && <p className="text-[11px] text-amber-200/80">A Rita está operando com {pct(risk.adaptive_mult)} do risco normal depois das últimas perdas.</p>}
      </div>
    </Card>
  );
}

/** Última daily e quando é a próxima. */
function DailyTeaser() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["daily", "teaser"], queryFn: () => api.get<any>("/api/daily?limit=1"), refetchInterval: 120000 });
  useEffect(
    () =>
      onLiveEvent((ev) => {
        if (ev.type === "daily") void qc.invalidateQueries({ queryKey: ["daily"] });
      }),
    [qc],
  );
  const last = q.data?.reports?.[0];
  return (
    <Card
      title={
        <span className="flex items-center gap-1.5">
          <ClipboardList className="h-3.5 w-3.5" /> Daily
        </span>
      }
      actions={
        <Link to="/daily" className="text-[11px] text-sky hover:underline">
          ver relatórios
        </Link>
      }
    >
      {last ? (
        <div className="text-sm">
          <div className="flex items-center gap-2 text-xs text-muted">
            <span>{new Date(`${last.day}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "long", day: "2-digit", month: "2-digit" })}</span>
            <span className={clsx("font-semibold tabular-nums", last.pnl > 0 ? "text-up" : last.pnl < 0 ? "text-down" : "")}>{signed(last.pnl)}</span>
          </div>
          <p className="mt-1 line-clamp-3 leading-snug text-slate-200">{last.summary}</p>
          {last.focus?.length > 0 && <p className="mt-2 line-clamp-2 text-xs text-gold">Foco: {last.focus.join(" · ")}</p>}
        </div>
      ) : (
        <p className="text-sm text-muted">A primeira daily acontece às {q.data?.time ?? "19:00"}: a equipe se reúne, avalia o dia e decide o que melhorar amanhã.</p>
      )}
      <p className="mt-2 text-[11px] text-muted">{q.data?.enabled === false ? "Daily automática desligada." : q.data?.next_at ? `Próxima: ${dateTime(q.data.next_at)}` : ""}</p>
    </Card>
  );
}

function Feed() {
  const { activity } = useLive();
  const items = useMemo(() => [...activity].reverse(), [activity]);
  return (
    <div className="scroll-thin min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
      {items.length === 0 && <p className="p-4 text-center text-sm text-muted">Nada por aqui ainda. Ligue o escritório para ver a equipe trabalhando.</p>}
      {items.map((a, i) => (
        <div key={`${a.id ?? i}-${a.ts}`} className="flex gap-2 text-sm">
          {a.agent !== "system" ? <Avatar id={a.agent} size={28} className="shrink-0" /> : <div className="h-7 w-7 shrink-0 rounded-lg bg-panel2" />}
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[11px]">
              <b style={{ color: AGENT_COLORS[a.agent] }}>{AGENT_NAMES[a.agent] ?? a.agent}</b>
              <span className="text-muted">{time(a.ts)}</span>
              {a.level === "warning" && <Badge tone="gold">atenção</Badge>}
              {a.level === "error" && <Badge tone="red">erro</Badge>}
            </div>
            <p className="break-words leading-snug text-slate-200">{a.text}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

function PlanPanel() {
  const { office } = useLive();
  const plan: any[] = office.plan || [];
  const events: any[] = office.next_events || [];
  const ranking: any[] = office.ranking || [];
  const stop = office.risk?.day_stop;
  return (
    <div className="scroll-thin min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm">
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">SETUPS ATIVOS</h3>
        {plan.length === 0 && <p className="text-muted">{stop ? "Dia encerrado: nenhum setup até amanhã." : "Nenhum setup ativo agora."}</p>}
        {plan.map((p) => (
          <div key={p.profile_id} className="mb-2 rounded-lg border border-line bg-panel2 p-3">
            <div className="flex flex-wrap items-center gap-1.5">
              <b>{p.symbol}</b> <Badge tone="blue">{p.timeframe}</Badge> <Badge tone="purple">{DIRECTION_LABEL[p.direction] ?? p.direction}</Badge>
              {p.horizon && <Badge>{HORIZON[p.horizon] ?? p.horizon}</Badge>}
            </div>
            <div className="mt-1 text-slate-300">{p.strategy_name}</div>
            {p.reason && <div className="mt-1 text-xs text-muted">{p.reason}</div>}
            <div className="mt-2 text-[11px] text-muted">risco × {p.risk_mult}</div>
          </div>
        ))}
        {office.plan_rationale && <p className="mt-2 text-xs italic text-slate-400">“{office.plan_rationale}”</p>}
      </div>
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">PRÓXIMOS EVENTOS</h3>
        {events.length === 0 && <p className="text-muted">Calendário sem eventos fortes nas próximas horas.</p>}
        {events.map((e) => (
          <div key={e.title + e.ts} className="flex items-center gap-2 py-1">
            <Badge tone={e.impact === "High" ? "red" : "gold"}>{e.currency}</Badge>
            <span className="flex-1">{e.title}</span>
            <span className="text-muted">{time(e.ts)}</span>
          </div>
        ))}
      </div>
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">TOP DO RANKING</h3>
        {ranking.map((r, i) => (
          <div key={i} className="flex items-center gap-2 py-1">
            <span className="w-4 text-muted">{i + 1}</span>
            <span className="flex-1">
              {r.strategy_name} · {r.symbol} {r.timeframe}
            </span>
            <span className="tabular-nums text-up">{pct(r.metrics?.win_rate)}</span>
          </div>
        ))}
        <Link to="/estrategias" className="mt-2 inline-block text-xs text-sky hover:underline">
          ver ranking completo →
        </Link>
      </div>
    </div>
  );
}

function AgentPanel({ id, onPick }: { id?: string; onPick: (id: string) => void }) {
  const live = useLive();
  const q = useQuery({ queryKey: ["agent", id], queryFn: () => api.get<any>(`/api/agents/${id}`), enabled: !!id, refetchInterval: 15000 });
  const talks = useMemo(() => (id ? live.messages.filter((m) => m.sender === id || m.recipient === id).slice(-6) : []), [live.messages, id]);
  if (!id)
    return (
      <div className="p-4">
        <p className="mb-3 text-sm text-muted">Toque em um agente no escritório (ou escolha abaixo) para ver quem ele é, o que está fazendo e com quem está falando.</p>
        <div className="grid grid-cols-4 gap-2">
          {TEAM_ORDER.filter((a) => live.agents[a]).map((a) => (
            <button key={a} onClick={() => onPick(a)} className="flex flex-col items-center gap-1 rounded-lg p-2 text-[11px] hover:bg-panel2">
              <Avatar id={a} size={36} /> {live.agents[a].name}
            </button>
          ))}
        </div>
      </div>
    );
  const a = live.agents[id];
  const d = q.data;
  const persona = a?.persona ?? d?.persona;
  return (
    <div className="scroll-thin min-h-0 flex-1 space-y-4 overflow-y-auto p-4 text-sm">
      <div className="flex items-center gap-3">
        <Avatar id={id} size={56} />
        <div className="min-w-0">
          <div className="font-semibold">
            <span style={{ color: AGENT_COLORS[id] }}>{a?.name}</span> <span className="text-muted">· {a?.role}</span>
          </div>
          {persona?.title && <div className="text-xs italic text-slate-300">{persona.title}</div>}
          <div className="mt-1 flex flex-wrap gap-1">
            {a?.uses_ai ? <Badge tone="green">usa IA</Badge> : <Badge>sem IA</Badge>}
            <Badge tone={a?.state === "working" ? "gold" : a?.state === "alert" || a?.state === "error" ? "red" : "slate"}>{STATE_LABEL[a?.state ?? ""] ?? a?.state}</Badge>
          </div>
        </div>
      </div>
      <p className="rounded-lg bg-panel2 px-3 py-2 text-slate-200">
        {a?.status_emoji} {a?.task}
      </p>
      {persona && (
        <div>
          <h3 className="mb-1.5 font-pixel text-[9px] text-gold">PERSONALIDADE</h3>
          <p className="text-xs leading-relaxed text-slate-300">{persona.bio}</p>
          <div className="mt-2 flex flex-wrap gap-1">
            {persona.traits.map((t: string) => (
              <Badge key={t} tone="purple">
                {t}
              </Badge>
            ))}
          </div>
          {persona.catchphrases?.[0] && <p className="mt-2 text-xs italic text-muted">“{persona.catchphrases[0]}”</p>}
        </div>
      )}
      <div>
        <h3 className="mb-2 flex items-center gap-1.5 font-pixel text-[9px] text-gold">
          <MessagesSquare className="h-3 w-3" /> CONVERSAS
        </h3>
        {talks.length === 0 ? (
          <p className="text-xs text-muted">Nenhuma conversa recente.</p>
        ) : (
          <div className="space-y-2">
            {talks.map((m, i) => (
              <MessageRow key={`${m.id ?? i}-${m.ts}`} m={m} />
            ))}
          </div>
        )}
      </div>
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">SKILLS</h3>
        {(d?.skills || [])
          .slice()
          .sort((x: any, y: any) => y.xp - x.xp)
          .slice(0, 6)
          .map((s: any) => (
            <div key={s.key} className="mb-2">
              <div className="flex justify-between text-xs">
                <span title={s.description}>{s.name}</span>
                <span className="text-gold">nv {s.level}</span>
              </div>
              <Bar value={s.pct} />
            </div>
          ))}
        <Link to="/agentes" className="text-xs text-sky hover:underline">
          ver todas as skills →
        </Link>
      </div>
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">ÚLTIMAS AÇÕES</h3>
        {(d?.activity || []).slice(0, 10).map((x: any, i: number) => (
          <div key={i} className="border-b border-line/60 py-1.5 text-xs">
            <span className="text-muted">{time(x.ts)}</span> {x.text}
          </div>
        ))}
      </div>
    </div>
  );
}
