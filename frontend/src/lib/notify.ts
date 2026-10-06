// Avisos rápidos (canto da tela): resultado das ações e erros que antes passavam em silêncio.
import { useSyncExternalStore } from "react";

export interface Toast {
  id: number;
  tone: "ok" | "error" | "info";
  text: string;
}

let toasts: Toast[] = [];
let next = 1;
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((l) => l());
}

export function dismiss(id: number) {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
}

export function notify(text: string, tone: Toast["tone"] = "ok") {
  const id = next++;
  toasts = [...toasts.slice(-3), { id, tone, text }];
  emit();
  setTimeout(() => dismiss(id), tone === "error" ? 8000 : 4000);
}

export const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Roda uma ação da tela: mostra o erro se falhar e, se pedido, um aviso de sucesso. Devolve se deu certo. */
export async function act<T>(fn: () => Promise<T>, ok?: string | ((r: T) => string | undefined)): Promise<boolean> {
  try {
    const r = await fn();
    const msg = typeof ok === "function" ? ok(r) : ok;
    if (msg) notify(msg, "ok");
    return true;
  } catch (err) {
    notify(errorText(err), "error");
    return false;
  }
}

export function useToasts(): Toast[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => toasts,
  );
}
