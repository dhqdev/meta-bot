// Conexão em tempo real com o escritório (WebSocket /ws) + store simples.
import { useSyncExternalStore } from "react";
import type { AgentView } from "../office/engine";

export interface ActivityItem {
  id?: number | null;
  ts: string;
  agent: string;
  kind: string;
  level: string;
  text: string;
}

export interface TeamMessage {
  id?: number | null;
  ts: string;
  sender: string;
  recipient: string;
  kind: string;
  text: string;
  data?: Record<string, any>;
}

export interface Persona {
  title: string;
  bio: string;
  traits: string[];
  voice: string;
  catchphrases: string[];
}

export interface LiveState {
  connected: boolean;
  agents: Record<string, AgentView & { uses_ai?: boolean; emoji?: string; description?: string; persona?: Persona }>;
  system: { running?: boolean; mode?: string; data_source?: string; ai?: boolean; mt5?: Record<string, any> };
  office: Record<string, any>;
  activity: ActivityItem[];
  messages: TeamMessage[];
  lastDaily?: { day: string; summary: string; pnl: number; mood: string };
}

type Listener = () => void;
type EventListener = (ev: Record<string, any>) => void;

let state: LiveState = { connected: false, agents: {}, system: {}, office: {}, activity: [], messages: [] };
const listeners = new Set<Listener>();
const eventListeners = new Set<EventListener>();
let ws: WebSocket | null = null;
let retry = 0;
let stopped = true;

function set(patch: Partial<LiveState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

function onMessage(ev: Record<string, any>) {
  switch (ev.type) {
    case "snapshot": {
      const agents: LiveState["agents"] = {};
      for (const a of ev.agents || []) agents[a.id] = a;
      set({ agents, system: ev.system || {}, office: ev.office || {}, activity: (ev.activity || []).slice(-200), messages: (ev.messages || []).slice(-200) });
      break;
    }
    case "agent":
      set({ agents: { ...state.agents, [ev.id]: { ...state.agents[ev.id], ...ev } } });
      break;
    case "activity":
      set({ activity: [...state.activity.slice(-199), ev as ActivityItem] });
      break;
    case "message":
      set({ messages: [...state.messages.slice(-199), ev as TeamMessage] });
      break;
    case "daily":
      set({ lastDaily: { day: ev.day, summary: ev.summary, pnl: ev.pnl, mood: ev.mood } });
      break;
    case "office":
      set({ office: { ...state.office, ...(ev.data || {}) } });
      break;
    case "mt5":
      set({ system: { ...state.system, mt5: ev, data_source: ev.source ?? state.system.data_source } });
      break;
    case "system":
      set({ system: { ...state.system, running: ev.running } });
      break;
    case "account":
      set({ office: { ...state.office, account: ev } });
      break;
  }
  eventListeners.forEach((l) => l(ev));
}

function connect() {
  if (stopped) return;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    retry = 0;
    set({ connected: true });
  };
  ws.onmessage = (msg) => {
    try {
      onMessage(JSON.parse(msg.data));
    } catch {
      /* mensagem inválida: ignora */
    }
  };
  ws.onclose = () => {
    set({ connected: false });
    ws = null;
    if (!stopped) setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++));
  };
}

export function startLive() {
  if (!stopped) return;
  stopped = false;
  connect();
}

export function stopLive() {
  stopped = true;
  ws?.close();
}

export function patchSystem(patch: LiveState["system"]) {
  set({ system: { ...state.system, ...patch } });
}

export function useLive(): LiveState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}

export function onLiveEvent(fn: EventListener): () => void {
  eventListeners.add(fn);
  return () => eventListeners.delete(fn);
}

export function liveSnapshot(): LiveState {
  return state;
}
