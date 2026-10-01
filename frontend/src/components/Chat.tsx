import clsx from "clsx";
import { ArrowRight, Megaphone } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { time } from "../lib/format";
import type { TeamMessage } from "../lib/live";
import { AGENT_COLORS, AGENT_NAMES, Avatar } from "./ui";

/** Tipos de mensagem da conversa da equipe (mesmas cores dos balões no escritório). */
export const MESSAGE_KINDS: Record<string, { label: string; color: string; hint: string }> = {
  info: { label: "recado", color: "#8a97b1", hint: "aviso para o time" },
  pedido: { label: "pedido", color: "#f5b041", hint: "um agente pede algo a outro" },
  resposta: { label: "resposta", color: "#5dade2", hint: "resposta a um pedido ou aviso" },
  alerta: { label: "alerta", color: "#e74c3c", hint: "risco, evento forte ou problema" },
  comemoracao: { label: "comemoração", color: "#2ecc71", hint: "meta batida, estratégia evoluída" },
  daily: { label: "daily", color: "#f7dc6f", hint: "reunião do fim do dia" },
};

export const TEAM_ORDER = ["manager", "news", "schedule", "strategist", "risk", "cashier", "auditor", "infra"];

export const agentName = (id: string) => (id === "all" ? "equipe" : (AGENT_NAMES[id] ?? id));

function Recipient({ id }: { id: string }) {
  if (id === "all")
    return (
      <span className="inline-flex items-center gap-1 text-slate-300">
        <Megaphone className="h-3 w-3" /> equipe
      </span>
    );
  return <b style={{ color: AGENT_COLORS[id] }}>{agentName(id)}</b>;
}

/** Uma mensagem: quem falou → para quem, tipo (cor) e horário. */
export function MessageRow({ m }: { m: TeamMessage }) {
  const kind = MESSAGE_KINDS[m.kind] ?? MESSAGE_KINDS.info;
  return (
    <div className="pop-in flex gap-2">
      <Avatar id={m.sender} size={30} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-1.5 text-[11px] leading-5">
          <b style={{ color: AGENT_COLORS[m.sender] }}>{agentName(m.sender)}</b>
          <ArrowRight className="h-3 w-3 text-muted" />
          <Recipient id={m.recipient} />
          <span className="ml-auto flex items-center gap-1.5 text-muted">
            <span style={{ color: kind.color }}>{kind.label}</span>
            {time(m.ts)}
          </span>
        </div>
        <div className="mt-0.5 break-words rounded-lg rounded-tl-sm border-l-2 bg-panel2 px-2.5 py-1.5 text-sm leading-snug text-slate-100" style={{ borderLeftColor: kind.color }}>
          {m.text}
        </div>
      </div>
    </div>
  );
}

/** Conversa da equipe, estilo chat: mais novas embaixo, filtro por agente e rolagem automática. */
export function TeamChat({ messages, focus }: { messages: TeamMessage[]; focus?: string }) {
  const [who, setWho] = useState<string | undefined>(focus);
  const [onlyImportant, setOnlyImportant] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  useEffect(() => setWho(focus), [focus]);
  const list = useMemo(
    () =>
      messages.filter(
        (m) => (!who || m.sender === who || m.recipient === who) && (!onlyImportant || m.kind === "alerta" || m.kind === "comemoracao" || m.kind === "daily"),
      ),
    [messages, who, onlyImportant],
  );
  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [list]);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="no-scrollbar flex items-center gap-1 overflow-x-auto border-b border-line px-2 py-1.5">
        <button onClick={() => setWho(undefined)} className={clsx("shrink-0 rounded-md px-2 py-1 text-[11px]", !who ? "bg-panel2 text-gold" : "text-muted hover:text-slate-200")}>
          todos
        </button>
        {TEAM_ORDER.map((id) => (
          <button
            key={id}
            onClick={() => setWho(who === id ? undefined : id)}
            title={`Só as conversas de ${agentName(id)}`}
            className={clsx("shrink-0 rounded-md p-0.5 transition", who === id ? "bg-panel2 ring-1 ring-gold" : "opacity-70 hover:opacity-100")}
          >
            <Avatar id={id} size={22} />
          </button>
        ))}
        <button
          onClick={() => setOnlyImportant((v) => !v)}
          title="Só alertas, comemorações e daily"
          className={clsx("ml-auto shrink-0 rounded-md px-2 py-1 text-[11px]", onlyImportant ? "bg-panel2 text-gold" : "text-muted hover:text-slate-200")}
        >
          importantes
        </button>
      </div>
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
        className="scroll-thin min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3"
      >
        {list.length === 0 && (
          <p className="p-4 text-center text-sm text-muted">
            {messages.length ? "Nenhuma mensagem com esse filtro." : "A equipe ainda não conversou. Ligue o escritório: eles se cumprimentam, trocam pedidos (sinal → aprovação → lote → ordem) e avisam o time sobre riscos e metas."}
          </p>
        )}
        {list.map((m, i) => (
          <MessageRow key={`${m.id ?? i}-${m.ts}`} m={m} />
        ))}
      </div>
    </div>
  );
}

/** Uma fala da reunião (daily). */
export function TranscriptLine({ agent, text, role }: { agent: string; text: string; role?: string }) {
  const host = agent === "manager";
  return (
    <div className={clsx("flex gap-2.5", host && "rounded-lg bg-amber-500/5 p-1.5 -mx-1.5")}>
      <Avatar id={agent} size={34} className="mt-0.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="text-[11px]">
          <b style={{ color: AGENT_COLORS[agent] }}>{agentName(agent)}</b>
          {role && <span className="text-muted"> · {role}</span>}
        </div>
        <p className="mt-0.5 rounded-lg rounded-tl-sm bg-panel2 px-3 py-2 text-sm leading-relaxed text-slate-100">{text}</p>
      </div>
    </div>
  );
}
