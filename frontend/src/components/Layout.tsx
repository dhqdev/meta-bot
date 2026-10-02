import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Bot, Building2, ClipboardList, Download, LogOut, Power, Receipt, Settings, Sparkles, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { signedCash } from "../lib/format";
import { patchSystem, useLive } from "../lib/live";
import { Badge } from "./ui";

const NAV = [
  { to: "/", label: "Escritório", icon: Building2 },
  { to: "/agentes", label: "Agentes", icon: Users },
  { to: "/daily", label: "Daily", icon: ClipboardList },
  { to: "/estrategias", label: "Estratégias", icon: Sparkles },
  { to: "/operacoes", label: "Operações", icon: Receipt },
  { to: "/config", label: "Config.", icon: Settings },
];

/** Resultado do dia e se a equipe já parou (meta batida ou limite de perda). */
export function DayGoalChip() {
  const risk = useLive().office.risk;
  if (!risk || risk.day_pnl == null) return null;
  const cur = risk.currency;
  if (risk.day_stop === "target") return <Badge tone="green">🎯 meta do dia batida · {signedCash(risk.day_stop_pnl ?? risk.day_pnl, cur)}</Badge>;
  if (risk.day_stop === "loss") return <Badge tone="red">⛔ limite do dia · {signedCash(risk.day_stop_pnl ?? risk.day_pnl, cur)}</Badge>;
  const pnl = Number(risk.day_pnl) || 0;
  return <Badge tone={pnl > 0 ? "green" : pnl < 0 ? "red" : "slate"}>hoje {signedCash(pnl, cur)}</Badge>;
}

export function StatusChips() {
  const { system, connected } = useLive();
  const mt5 = system.mt5 || {};
  const mt5Tone = mt5.connected ? "green" : mt5.configured ? "red" : "slate";
  const mt5Label = mt5.connected ? `MT5 ${mt5.server || "conectado"}` : mt5.configured ? "MT5 fora do ar" : "sem MT5";
  return (
    <div className="no-scrollbar flex items-center gap-1.5 overflow-x-auto">
      <Badge tone={system.mode === "live" ? "gold" : "blue"}>{system.mode === "live" ? "CONTA MT5" : "SIMULADO"}</Badge>
      <DayGoalChip />
      {system.data_source === "real" && (
        <Badge tone={mt5.feed?.ok === false ? "red" : "green"}>{mt5.feed?.ok === false ? "preços reais fora do ar" : "preços reais"}</Badge>
      )}
      {system.data_source === "synthetic" && <Badge tone="purple">mercado simulado</Badge>}
      <Badge tone={mt5Tone}>{mt5Label}</Badge>
      <Badge tone={system.ai ? "green" : "slate"}>
        <Bot className="h-3 w-3" /> {system.ai ? "IA ligada" : "sem IA"}
      </Badge>
      {!connected && <Badge tone="red">reconectando…</Badge>}
    </div>
  );
}

export function PowerButton({ compact }: { compact?: boolean }) {
  const { system } = useLive();
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const running = !!system.running;
  return (
    <button
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          const res = await api.post<{ running: boolean }>("/api/system/running", { running: !running });
          patchSystem({ running: res.running });
          void qc.invalidateQueries();
        } finally {
          setBusy(false);
        }
      }}
      className={clsx(
        "flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 font-pixel text-[9px] uppercase transition disabled:opacity-60",
        running ? "bg-up text-ink hover:bg-emerald-300" : "bg-slate-700 text-slate-100 hover:bg-slate-600",
      )}
      title={running ? "Desligar: a equipe para de abrir operações (as abertas continuam protegidas)" : "Ligar o escritório"}
    >
      <Power className="h-4 w-4" />
      {!compact && <span className="hidden sm:inline">{running ? "Escritório aberto" : "Ligar escritório"}</span>}
    </button>
  );
}

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
}

/** "Instalar app": aparece quando o navegador permite instalar o PWA. */
function InstallButton() {
  const [event, setEvent] = useState<InstallPromptEvent | null>(null);
  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setEvent(e as InstallPromptEvent);
    };
    const onInstalled = () => setEvent(null);
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);
  if (!event) return null;
  return (
    <button
      onClick={async () => {
        await event.prompt();
        await event.userChoice.catch(() => null);
        setEvent(null);
      }}
      className="flex items-center gap-1.5 rounded-lg border border-line px-2.5 py-2 text-xs text-slate-200 hover:bg-panel"
      title="Instalar o META-BOT como aplicativo"
    >
      <Download className="h-4 w-4" />
      <span className="hidden sm:inline">Instalar app</span>
    </button>
  );
}

export function Layout() {
  const { logout, status } = useAuth();
  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-ink/95 pt-[env(safe-area-inset-top)] backdrop-blur">
        <div className="mx-auto flex max-w-[1600px] items-center gap-3 px-3 py-2 md:px-5">
          <div className="flex items-center gap-2">
            <div className="grid h-8 w-8 place-items-center rounded-lg bg-panel2">
              <img src="/favicon.svg" alt="" className="pixelated h-6 w-6" />
            </div>
            <span className="whitespace-nowrap font-pixel text-[11px] text-gold">META-BOT</span>
          </div>
          <nav className="ml-4 hidden items-center gap-1 lg:flex">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => clsx("flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm", isActive ? "bg-panel2 text-gold" : "text-slate-300 hover:bg-panel")}>
                <n.icon className="h-4 w-4" /> {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <div className="hidden 2xl:block">
              <StatusChips />
            </div>
            <InstallButton />
            <PowerButton />
            <button onClick={() => void logout()} className="rounded-lg p-2 text-muted hover:bg-panel" title={`Sair (${status?.email ?? ""})`}>
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
        <div className="border-t border-line px-3 py-1.5 2xl:hidden">
          <StatusChips />
        </div>
      </header>
      <main className="mx-auto w-full max-w-[1600px] flex-1 px-3 pb-[calc(5rem+env(safe-area-inset-bottom))] pt-4 md:px-5 lg:pb-8">
        <Outlet />
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-6 border-t border-line bg-ink/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.to === "/"}
            className={({ isActive }) => clsx("flex min-w-0 flex-col items-center gap-0.5 py-2 text-[10px] leading-tight", isActive ? "text-gold" : "text-slate-400")}
          >
            <n.icon className="h-5 w-5" />
            <span className="max-w-full truncate">{n.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
