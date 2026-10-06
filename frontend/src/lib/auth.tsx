import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, UNAUTHORIZED_EVENT } from "./api";

interface AuthStatus {
  needs_setup: boolean;
  logged_in: boolean;
  email: string | null;
  totp_enabled: boolean;
}

interface AuthCtx {
  loading: boolean;
  status: AuthStatus | null;
  /** O servidor não respondeu ao conferir a sessão. */
  offline: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>({ loading: true, status: null, offline: false, refresh: async () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const refresh = useCallback(async () => {
    try {
      setStatus(await api.get<AuthStatus>("/api/auth/status"));
      setOffline(false);
    } catch {
      // servidor fora do ar: mantém o que já se sabia (não derruba o dono para o login por uma queda de rede)
      setOffline(true);
    } finally {
      setLoading(false);
    }
  }, []);
  const logout = useCallback(async () => {
    await api.post("/api/auth/logout").catch(() => undefined);
    await refresh();
  }, [refresh]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  // sessão expirada no meio do uso: confere de novo e, se acabou mesmo, mostra o login
  useEffect(() => {
    const onUnauthorized = () => void refresh();
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [refresh]);
  return <Ctx.Provider value={{ loading, status, offline, refresh, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
