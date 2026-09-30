import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

interface AuthStatus {
  needs_setup: boolean;
  logged_in: boolean;
  email: string | null;
  totp_enabled: boolean;
}

interface AuthCtx {
  loading: boolean;
  status: AuthStatus | null;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthCtx>({ loading: true, status: null, refresh: async () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      setStatus(await api.get<AuthStatus>("/api/auth/status"));
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);
  const logout = useCallback(async () => {
    await api.post("/api/auth/logout");
    await refresh();
  }, [refresh]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  return <Ctx.Provider value={{ loading, status, refresh, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
