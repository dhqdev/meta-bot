export const money = (v: number | null | undefined, digits = 2) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const signed = (v: number | null | undefined, digits = 2) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${money(v, digits)}`);

export const pct = (v: number | null | undefined, digits = 0) =>
  v == null || Number.isNaN(v) ? "—" : `${(v * 100).toLocaleString("pt-BR", { minimumFractionDigits: digits, maximumFractionDigits: digits })}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: digits });

const TZ = "America/Sao_Paulo";

export const time = (iso: string | number | null | undefined) => {
  if (iso == null) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
};

export const dateTime = (iso: string | number | null | undefined) => {
  if (iso == null) return "—";
  const d = typeof iso === "number" ? new Date(iso * 1000) : new Date(iso);
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", timeZone: TZ });
};

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "agora";
  if (s < 3600) return `há ${Math.floor(s / 60)} min`;
  if (s < 86400) return `há ${Math.floor(s / 3600)} h`;
  return `há ${Math.floor(s / 86400)} d`;
};

export function saoPauloHour(): number {
  const parts = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: TZ }).formatToParts(new Date());
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? 12);
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return (h % 24) + m / 60;
}

export const DIRECTION_LABEL: Record<string, string> = { both: "compra e venda", long: "só compra", short: "só venda" };
export const EXIT_LABEL: Record<string, string> = {
  sl: "stop",
  tp: "alvo",
  be: "zero a zero",
  trailing: "trailing",
  tempo: "tempo",
  sinal: "sinal",
  "fim de semana": "fim de semana",
  "fim do pregão": "fim do pregão",
  revisao: "revisão do gerente",
  "limite do dia": "limite do dia",
  "meta do dia": "meta do dia",
};
