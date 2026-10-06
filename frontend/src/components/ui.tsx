import clsx from "clsx";
import { Loader2, X } from "lucide-react";
import { useEffect, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { dismiss, useToasts } from "../lib/notify";
import { portrait } from "../office/sprites";

export function Card({ title, actions, children, className, pad = true, fill = false }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; pad?: boolean; fill?: boolean }) {
  return (
    <section className={clsx("rounded-xl border border-line bg-panel shadow-lg shadow-black/20", fill && "flex min-h-0 flex-col", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-2.5">
          <h2 className="font-pixel text-[10px] uppercase tracking-wider text-gold">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={clsx(pad && "p-4", fill && "flex min-h-0 flex-1 flex-col")}>{children}</div>
    </section>
  );
}

type BtnVariant = "primary" | "ghost" | "danger" | "success" | "subtle";

export function Button({ variant = "primary", loading, className, children, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant; loading?: boolean }) {
  const styles: Record<BtnVariant, string> = {
    primary: "bg-gold text-ink hover:bg-amber-300",
    ghost: "border border-line text-slate-200 hover:bg-panel2",
    danger: "bg-down text-white hover:bg-red-500",
    success: "bg-up text-ink hover:bg-emerald-300",
    subtle: "bg-panel2 text-slate-200 hover:bg-line",
  };
  return (
    <button
      {...rest}
      disabled={rest.disabled || loading}
      className={clsx("inline-flex items-center justify-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition disabled:cursor-not-allowed disabled:opacity-50", styles[variant], className)}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" />}
      {children}
    </button>
  );
}

export function Badge({ children, tone = "slate", className, title }: { children: ReactNode; tone?: "slate" | "green" | "red" | "gold" | "blue" | "purple"; className?: string; title?: string }) {
  const tones = {
    slate: "bg-slate-700/40 text-slate-300 border-slate-600/40",
    green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
    red: "bg-red-500/15 text-red-300 border-red-500/30",
    gold: "bg-amber-500/15 text-amber-300 border-amber-500/30",
    blue: "bg-sky-500/15 text-sky-300 border-sky-500/30",
    purple: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  };
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-semibold", tones[tone], className)}>
      {children}
    </span>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "up" | "down" | "gold" }) {
  return (
    <div className="rounded-xl border border-line bg-panel px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={clsx("mt-1 text-xl font-bold tabular-nums", tone === "up" && "text-up", tone === "down" && "text-down", tone === "gold" && "text-gold")}>{value}</div>
      {hint && <div className="mt-0.5 text-xs text-muted">{hint}</div>}
    </div>
  );
}

export function Loading({ label = "Carregando…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted">
      <Loader2 className="h-4 w-4 animate-spin" /> {label}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">{children}</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">{error instanceof Error ? error.message : String(error)}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-semibold text-slate-300">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] leading-snug text-muted">{hint}</span>}
    </label>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={clsx("w-full rounded-lg border border-line bg-ink px-3 py-2 text-slate-100 outline-none placeholder:text-slate-500 focus:border-gold", props.className)} />;
}

export function Select({ value, onChange, options, className }: { value: string; onChange: (v: string) => void; options: Array<[string, string]>; className?: string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className={clsx(!/\bw-/.test(className ?? "") && "w-full", "rounded-lg border border-line bg-ink px-3 py-2 text-slate-100 outline-none focus:border-gold", className)}>
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </select>
  );
}

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label?: ReactNode; disabled?: boolean }) {
  return (
    <button type="button" disabled={disabled} onClick={() => onChange(!checked)} className="flex items-center gap-2 text-left text-sm disabled:opacity-50">
      <span className={clsx("relative inline-flex h-5 w-9 shrink-0 rounded-full transition", checked ? "bg-up" : "bg-slate-600")}>
        <span className={clsx("absolute top-0.5 h-4 w-4 rounded-full bg-white transition", checked ? "left-4.5" : "left-0.5")} />
      </span>
      {label}
    </button>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4" onClick={onClose}>
      <div className={clsx("max-h-[92vh] w-full overflow-y-auto rounded-t-2xl border border-line bg-panel p-5 sm:rounded-2xl", wide ? "max-w-4xl" : "max-w-lg")} onClick={(e) => e.stopPropagation()}>
        <div className="mb-4 flex items-center justify-between">
          <h3 className="font-pixel text-[11px] text-gold">{title}</h3>
          <button onClick={onClose} className="rounded p-1 text-muted hover:bg-panel2" aria-label="Fechar">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** Pede a senha antes de ações sensíveis (dinheiro real, chaves). */
export function PasswordPrompt({ open, title, description, confirmLabel = "Confirmar", onCancel, onConfirm }: { open: boolean; title: string; description: ReactNode; confirmLabel?: string; onCancel: () => void; onConfirm: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (!open) {
      setPassword("");
      setError(null);
    }
  }, [open]);
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <form
        className="space-y-4"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError(null);
          try {
            await onConfirm(password);
          } catch (err) {
            setError(err);
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="text-sm text-slate-300">{description}</div>
        <Field label="Sua senha">
          <Input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </Field>
        <ErrorBox error={error} />
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancelar
          </Button>
          <Button type="submit" loading={busy} disabled={!password}>
            {confirmLabel}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

const portraits = new Map<string, string>();
export function Avatar({ id, size = 40, className }: { id: string; size?: number; className?: string }) {
  let src = portraits.get(id);
  if (!src) {
    src = portrait(id, 48);
    portraits.set(id, src);
  }
  // tamanho fixo: dentro de linhas flex o navegador esticava o retrato
  return <img src={src} width={size} height={size} alt="" style={{ width: size, height: size }} className={clsx("pixelated shrink-0 self-start rounded-lg bg-panel2", className)} />;
}

export function Bar({ value, tone = "gold" }: { value: number; tone?: "gold" | "green" | "red" | "blue" }) {
  const colors = { gold: "bg-gold", green: "bg-up", red: "bg-down", blue: "bg-sky" };
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-slate-700/60">
      <div className={clsx("h-full rounded-full", colors[tone])} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
    </div>
  );
}

export const AGENT_COLORS: Record<string, string> = {
  news: "#1abc9c",
  schedule: "#e67e22",
  strategist: "#8e44ad",
  manager: "#95a5a6",
  risk: "#c0392b",
  cashier: "#27ae60",
  auditor: "#2980b9",
  infra: "#7f8c8d",
  system: "#f5b041",
};

export const AGENT_NAMES: Record<string, string> = {
  news: "Nina",
  schedule: "Hugo",
  strategist: "Estela",
  manager: "Gustavo",
  risk: "Rita",
  cashier: "Caio",
  auditor: "Aurora",
  infra: "Tito",
  system: "Sistema",
};

/** Avisos rápidos das ações (sucesso e erro), acima da barra de baixo no celular. */
export function Toasts() {
  const toasts = useToasts();
  if (!toasts.length) return null;
  const tone = { ok: "border-emerald-500/40 bg-emerald-950/95 text-emerald-100", error: "border-red-500/50 bg-red-950/95 text-red-100", info: "border-sky-500/40 bg-slate-900/95 text-sky-100" };
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-[calc(5rem+env(safe-area-inset-bottom))] z-[60] flex flex-col items-center gap-2 px-3 lg:bottom-6 lg:items-end lg:px-6" role="status" aria-live="polite">
      {toasts.map((t) => (
        <button key={t.id} onClick={() => dismiss(t.id)} className={clsx("pointer-events-auto max-w-md rounded-lg border px-4 py-2.5 text-left text-sm shadow-2xl shadow-black/50", tone[t.tone])}>
          {t.tone === "error" ? "⚠️ " : t.tone === "ok" ? "✓ " : ""}
          {t.text}
        </button>
      ))}
    </div>
  );
}
