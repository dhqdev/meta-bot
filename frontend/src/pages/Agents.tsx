import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Trash2 } from "lucide-react";
import { useState } from "react";
import { AGENT_COLORS, Avatar, Badge, Bar, Button, Card, Empty, Loading, Modal } from "../components/ui";
import { api } from "../lib/api";
import { ago, dateTime } from "../lib/format";

const JOB_LABELS: Record<string, string> = {
  health: "checar MT5",
  warm: "atualizar candles",
  housekeeping: "manutenção",
  fetch: "ler notícias",
  classify: "classificar com IA",
  evaluate: "conferir previsões",
  calendar: "atualizar calendário",
  hours: "mapa de horários",
  ranking: "rodar ranking",
  evolution: "evoluir estratégias",
  revalidate: "revalidar",
  decide: "montar plano",
  guard: "checar limites",
  equity: "registrar patrimônio",
  exits_review: "rever gestão de saída",
  prune: "revisar lições",
};

export function AgentsPage() {
  const agents = useQuery({ queryKey: ["agents"], queryFn: () => api.get<any[]>("/api/agents"), refetchInterval: 15000 });
  const lessons = useQuery({ queryKey: ["lessons"], queryFn: () => api.get<any[]>("/api/lessons") });
  const [open, setOpen] = useState<string | null>(null);
  const qc = useQueryClient();
  if (agents.isLoading) return <Loading />;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="font-pixel text-sm text-gold">A EQUIPE</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Cada agente tem skills que sobem de nível com trabalho e resultado real: backtests aprovados, evoluções confirmadas fora da amostra, notícias que acertaram a direção do preço, operações bem geridas. Os que usam IA (Claude) recebem no prompt o playbook da função e as lições registradas pela Auditora.
        </p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-4">
        {agents.data?.map((a) => {
          const top = [...a.skills].sort((x, y) => y.xp - x.xp).slice(0, 5);
          return (
            <Card key={a.id} className="flex flex-col">
              <div className="flex items-start gap-3">
                <Avatar id={a.id} size={56} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <b style={{ color: AGENT_COLORS[a.id] }}>{a.name}</b>
                    <span className="text-xs text-muted">{a.role}</span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {a.uses_ai ? <Badge tone="green">IA</Badge> : <Badge>sem IA</Badge>}
                    <Badge tone="gold">nível médio {a.level}</Badge>
                  </div>
                  <p className="mt-2 text-xs text-slate-300">{a.task}</p>
                </div>
              </div>
              <p className="mt-3 text-xs leading-relaxed text-muted">{a.description}</p>
              <div className="mt-3 space-y-2">
                {top.map((s: any) => (
                  <div key={s.key}>
                    <div className="flex justify-between text-xs">
                      <span title={s.description}>{s.name}</span>
                      <span className="text-gold">
                        nv {s.level} · {s.xp} xp
                      </span>
                    </div>
                    <Bar value={s.pct} />
                  </div>
                ))}
              </div>
              <div className="mt-auto flex flex-wrap gap-1.5 pt-4">
                <Button variant="subtle" className="text-xs" onClick={() => setOpen(a.id)}>
                  Detalhes
                </Button>
                {a.jobs.map((j: string) => (
                  <Button
                    key={j}
                    variant="ghost"
                    className="text-xs"
                    onClick={async () => {
                      await api.post(`/api/agents/${a.id}/run?job=${j}`);
                      setTimeout(() => void qc.invalidateQueries({ queryKey: ["agents"] }), 1500);
                    }}
                  >
                    <Play className="h-3 w-3" /> {JOB_LABELS[j] ?? j}
                  </Button>
                ))}
              </div>
            </Card>
          );
        })}
      </div>
      <Card title="Lições que a equipe segue">
        {!lessons.data?.length && <Empty>A Auditora registra lições quando encontra padrões nas operações reais (ex.: horário com perdas seguidas, estratégia abaixo do backtest).</Empty>}
        <div className="divide-y divide-line">
          {lessons.data?.map((l) => (
            <div key={l.id} className="flex items-start gap-3 py-2 text-sm">
              <Avatar id={l.agent === "all" ? "auditor" : l.agent} size={24} />
              <div className="flex-1">
                <p>{l.text}</p>
                <p className="text-[11px] text-muted">
                  para {l.agent} · {ago(l.ts)} · peso {l.score}
                </p>
              </div>
              <button
                className="rounded p-1 text-muted hover:bg-panel2 hover:text-down"
                title="Desativar lição"
                onClick={async () => {
                  await api.del(`/api/lessons/${l.id}`);
                  void lessons.refetch();
                }}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      </Card>
      <AgentModal id={open} onClose={() => setOpen(null)} />
    </div>
  );
}

function AgentModal({ id, onClose }: { id: string | null; onClose: () => void }) {
  const q = useQuery({ queryKey: ["agent", id], queryFn: () => api.get<any>(`/api/agents/${id}`), enabled: !!id });
  const d = q.data;
  return (
    <Modal open={!!id} onClose={onClose} title={d ? `${d.name} · ${d.role}` : "Agente"}>
      {!d ? (
        <Loading />
      ) : (
        <div className="space-y-4 text-sm">
          <div>
            <h4 className="mb-2 font-pixel text-[9px] text-gold">TODAS AS SKILLS</h4>
            <div className="space-y-2">
              {d.skills.map((s: any) => (
                <div key={s.key}>
                  <div className="flex justify-between text-xs">
                    <span>{s.name}</span>
                    <span className="text-gold">
                      nv {s.level} · {s.xp} xp
                    </span>
                  </div>
                  <Bar value={s.pct} />
                  <p className="mt-0.5 text-[11px] text-muted">{s.description}</p>
                  {s.params && Object.keys(s.params).length > 0 && <pre className="mt-1 overflow-x-auto rounded bg-ink p-2 text-[10px] text-slate-400">{JSON.stringify(s.params, null, 1).slice(0, 400)}</pre>}
                </div>
              ))}
            </div>
          </div>
          <div>
            <h4 className="mb-2 font-pixel text-[9px] text-gold">EVOLUÇÃO</h4>
            {d.skill_events.filter((e: any) => e.kind !== "xp").length === 0 && <p className="text-muted">Ainda sem subidas de nível ou evoluções.</p>}
            {d.skill_events
              .filter((e: any) => e.kind !== "xp")
              .map((e: any, i: number) => (
                <div key={i} className="border-b border-line/60 py-1.5 text-xs">
                  <Badge tone={e.kind === "evolved" ? "purple" : e.kind === "levelup" ? "gold" : "red"}>{e.kind === "evolved" ? "evoluiu" : e.kind === "levelup" ? "subiu de nível" : "rebaixada"}</Badge> <span className="text-muted">{dateTime(e.ts)}</span>
                  <p className="mt-0.5">{e.text}</p>
                </div>
              ))}
          </div>
          <div>
            <h4 className="mb-2 font-pixel text-[9px] text-gold">ATIVIDADE</h4>
            {d.activity.slice(0, 30).map((x: any, i: number) => (
              <div key={i} className="border-b border-line/60 py-1 text-xs">
                <span className="text-muted">{dateTime(x.ts)}</span> {x.text}
              </div>
            ))}
          </div>
        </div>
      )}
    </Modal>
  );
}
