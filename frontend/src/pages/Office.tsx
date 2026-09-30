import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import { AGENT_COLORS, AGENT_NAMES, Avatar, Badge, Bar, Card, Stat } from "../components/ui";
import { api } from "../lib/api";
import { DIRECTION_LABEL, money, pct, signed, time } from "../lib/format";
import { useLive } from "../lib/live";
import { OfficeCanvas } from "../office/OfficeCanvas";

interface Summary {
  win_rate: number;
  trades: number;
  today_pnl: number;
  pnl: number;
}

export function OfficePage() {
  const live = useLive();
  const [selected, setSelected] = useState<string | undefined>();
  const [tab, setTab] = useState<"feed" | "plan" | "agent">("feed");
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api.get<Summary>("/api/trades/summary?days=30"), refetchInterval: 30000 });
  const open = useQuery({ queryKey: ["open-trades"], queryFn: () => api.get<any[]>("/api/trades/open"), refetchInterval: 10000 });
  const acc = live.office.account || {};
  const risk = live.office.risk || {};

  useEffect(() => {
    if (selected) setTab("agent");
  }, [selected]);

  return (
    <div className="space-y-4">
      {!live.system.running && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
          <span>
            O escritório está <b>fechado</b>: a equipe está no lounge. Toque em <b>Ligar escritório</b> no topo para os agentes começarem a trabalhar (tudo começa no modo simulado).
          </span>
        </div>
      )}
      <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
        <div className="space-y-3">
          <OfficeCanvas selected={selected} onSelect={setSelected} />
          <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Patrimônio" value={money(acc.equity)} hint={`${acc.currency || ""} · ${live.system.mode === "live" ? "conta MT5" : "simulado"}`} tone="gold" />
            <Stat label="Hoje" value={signed(summary.data?.today_pnl)} tone={(summary.data?.today_pnl ?? 0) >= 0 ? "up" : "down"} />
            <Stat label="Acerto (30 dias)" value={pct(summary.data?.win_rate)} hint={`${summary.data?.trades ?? 0} operações`} />
            <Stat label="Posições abertas" value={open.data?.length ?? 0} hint={`limite ${risk.max_positions ?? "—"}`} />
            <Stat label="Risco livre hoje" value={risk.daily_room_pct != null ? `${money(risk.daily_room_pct, 1)}%` : "—"} hint={risk.kill_switch ? "TRAVA GERAL ATIVA" : risk.day_blocked ? "limite diário atingido" : "de perda diária"} tone={risk.kill_switch || risk.day_blocked ? "down" : undefined} />
          </div>
          {open.data && open.data.length > 0 && (
            <Card title="Posições abertas" pad={false}>
              <div className="divide-y divide-line">
                {open.data.map((t) => (
                  <div key={t.id} className="flex flex-wrap items-center gap-3 px-4 py-2 text-sm">
                    <Badge tone={t.direction === "buy" ? "green" : "red"}>{t.direction === "buy" ? "COMPRA" : "VENDA"}</Badge>
                    <b>{t.symbol}</b>
                    <span className="text-muted">{t.strategy_name} · {t.timeframe}</span>
                    <span className="ml-auto tabular-nums">{t.volume} lote(s)</span>
                    <span className={clsx("tabular-nums font-semibold", (t.open_pnl ?? 0) >= 0 ? "text-up" : "text-down")}>{signed(t.open_pnl)} ({signed(t.open_r)}R)</span>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
        <Card
          className="h-[520px] xl:sticky xl:top-20 xl:h-[calc(100vh-110px)]"
          fill
          pad={false}
          title={
            <div className="flex gap-1">
              {(["feed", "plan", "agent"] as const).map((k) => (
                <button key={k} onClick={() => setTab(k)} className={clsx("rounded px-2 py-1", tab === k ? "bg-panel2 text-gold" : "text-muted")}>
                  {k === "feed" ? "Ao vivo" : k === "plan" ? "Plano" : "Agente"}
                </button>
              ))}
            </div>
          }
        >
          {tab === "feed" && <Feed />}
          {tab === "plan" && <PlanPanel />}
          {tab === "agent" && <AgentPanel id={selected} onPick={setSelected} />}
        </Card>
      </div>
    </div>
  );
}

function Feed() {
  const { activity } = useLive();
  const ref = useRef<HTMLDivElement>(null);
  const items = useMemo(() => [...activity].reverse(), [activity]);
  return (
    <div ref={ref} className="scroll-thin flex-1 space-y-2 overflow-y-auto p-3">
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
  return (
    <div className="scroll-thin flex-1 space-y-4 overflow-y-auto p-4 text-sm">
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">SETUPS ATIVOS</h3>
        {plan.length === 0 && <p className="text-muted">Nenhum setup ativo agora.</p>}
        {plan.map((p) => (
          <div key={p.profile_id} className="mb-2 rounded-lg border border-line bg-panel2 p-3">
            <div className="flex items-center gap-2">
              <b>{p.symbol}</b> <Badge tone="blue">{p.timeframe}</Badge> <Badge tone="purple">{DIRECTION_LABEL[p.direction] ?? p.direction}</Badge>
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
  if (!id)
    return (
      <div className="p-4">
        <p className="mb-3 text-sm text-muted">Clique em um agente no escritório (ou escolha abaixo).</p>
        <div className="grid grid-cols-4 gap-2">
          {Object.values(live.agents).map((a) => (
            <button key={a.id} onClick={() => onPick(a.id)} className="flex flex-col items-center gap-1 rounded-lg p-2 text-[11px] hover:bg-panel2">
              <Avatar id={a.id} size={36} /> {a.name}
            </button>
          ))}
        </div>
      </div>
    );
  const a = live.agents[id];
  const d = q.data;
  return (
    <div className="scroll-thin flex-1 space-y-4 overflow-y-auto p-4 text-sm">
      <div className="flex items-center gap-3">
        <Avatar id={id} size={56} />
        <div>
          <div className="font-semibold">
            {a?.name} <span className="text-muted">· {a?.role}</span>
          </div>
          <div className="mt-1 flex gap-1">
            {a?.uses_ai ? <Badge tone="green">usa IA</Badge> : <Badge>sem IA</Badge>}
            <Badge tone={a?.state === "working" ? "gold" : a?.state === "alert" || a?.state === "error" ? "red" : "slate"}>{a?.state}</Badge>
          </div>
        </div>
      </div>
      <p className="text-slate-300">{a?.task}</p>
      <p className="text-xs text-muted">{a?.description}</p>
      <div>
        <h3 className="mb-2 font-pixel text-[9px] text-gold">SKILLS</h3>
        {(d?.skills || [])
          .slice()
          .sort((x: any, y: any) => y.xp - x.xp)
          .slice(0, 8)
          .map((s: any) => (
            <div key={s.key} className="mb-2">
              <div className="flex justify-between text-xs">
                <span>{s.name}</span>
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
        {(d?.activity || []).slice(0, 12).map((x: any, i: number) => (
          <div key={i} className="border-b border-line/60 py-1.5 text-xs">
            <span className="text-muted">{time(x.ts)}</span> {x.text}
          </div>
        ))}
      </div>
    </div>
  );
}
