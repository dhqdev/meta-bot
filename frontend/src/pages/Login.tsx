import { useState } from "react";
import { Button, ErrorBox, Field, Input } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

export function LoginPage() {
  const { status, refresh, offline } = useAuth();
  const setup = !!status?.needs_setup;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (setup) {
        await api.post("/api/auth/setup", { email, password, setup_code: code });
      } else {
        const res = await api.post<{ ok: boolean; needs_code?: boolean }>("/api/auth/login", { email, password, code: code || undefined });
        if (res.needs_code) {
          setNeedsCode(true);
          return;
        }
      }
      await refresh();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-full place-items-center bg-[radial-gradient(ellipse_at_top,#1b2436,#0d121c)] p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-2xl border border-line bg-panel p-6 shadow-2xl">
        <div className="text-center">
          <img src="/favicon.svg" alt="" className="pixelated mx-auto mb-3 h-14 w-14" />
          <h1 className="font-pixel text-sm text-gold">META-BOT</h1>
          <p className="mt-2 text-sm text-muted">{setup ? "Primeiro acesso: crie a conta do dono do sistema." : "Escritório de trading multiagente"}</p>
        </div>
        <Field label="E-mail">
          <Input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </Field>
        <Field label="Senha" hint={setup ? "Pelo menos 10 caracteres, com letras e números." : undefined}>
          <Input type="password" autoComplete={setup ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} required />
        </Field>
        {setup && (
          <Field label="Código de configuração" hint="Aparece nos logs do backend na primeira inicialização (Portainer → container backend → Logs).">
            <Input value={code} onChange={(e) => setCode(e.target.value.toUpperCase())} required />
          </Field>
        )}
        {!setup && needsCode && (
          <Field label="Código do app autenticador">
            <Input inputMode="numeric" autoFocus value={code} onChange={(e) => setCode(e.target.value)} />
          </Field>
        )}
        {offline && !status && <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100">O servidor não respondeu. Confira se o backend está no ar e tente de novo.</div>}
        <ErrorBox error={error} />
        <Button type="submit" loading={busy} className="w-full">
          {setup ? "Criar conta e entrar" : "Entrar"}
        </Button>
      </form>
    </div>
  );
}
