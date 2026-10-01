import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { ChevronDown, ExternalLink, Lock, Plus, RotateCcw, Save, Trash2, Undo2 } from "lucide-react";
import QRCode from "qrcode";
import { useEffect, useState, type ReactNode } from "react";
import { Avatar, Badge, Button, Card, ErrorBox, Field, Input, Loading, PasswordPrompt, Select, Switch } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { dateTime, money } from "../lib/format";
import { patchSystem, useLive } from "../lib/live";

type Cfg = Record<string, any>;
type Setter = (k: string, v: any) => void;

interface FieldDef {
  key: string;
  label: string;
  hint: ReactNode;
  type?: "number" | "switch" | "select" | "time" | "list" | "impacts";
  min?: number;
  max?: number;
  int?: boolean;
  nullable?: boolean;
  suffix?: string;
  options?: Array<[string, string]>;
}

interface Section {
  id: string;
  agent: string;
  title: string;
  about: string;
  fields: FieldDef[];
  extra?: "feeds";
}

// ------------------------------------------------------------------ textos
const TIMEFRAME_INFO: Record<string, string> = {
  M5: "5 min · scalper",
  M15: "15 min · day trade rápido",
  M30: "30 min · day trade",
  H1: "1 hora · day trade",
  H4: "4 horas · fica 1 a 3 dias",
  D1: "diário · posição longa",
};

const PROFILES: Array<{ key: string; label: string; desc: string; values: Cfg }> = [
  {
    key: "conservador",
    label: "Conservador",
    desc: "0,25% por operação · para em −1,5% no dia · meta +1% · até 2 posições",
    values: { risk_per_trade_pct: 0.25, daily_loss_limit: 1.5, daily_loss_unit: "percent", daily_profit_target: 1, daily_profit_unit: "percent", max_open_positions: 2, max_drawdown_pct: 8 },
  },
  {
    key: "moderado",
    label: "Moderado",
    desc: "0,5% por operação · para em −3% · meta +2% · até 3 posições",
    values: { risk_per_trade_pct: 0.5, daily_loss_limit: 3, daily_loss_unit: "percent", daily_profit_target: 2, daily_profit_unit: "percent", max_open_positions: 3, max_drawdown_pct: 12 },
  },
  {
    key: "arrojado",
    label: "Arrojado",
    desc: "1% por operação · para em −5% · meta +4% · até 5 posições",
    values: { risk_per_trade_pct: 1, daily_loss_limit: 5, daily_loss_unit: "percent", daily_profit_target: 4, daily_profit_unit: "percent", max_open_positions: 5, max_drawdown_pct: 20 },
  },
];

const SECTIONS: Section[] = [
  {
    id: "risk",
    agent: "risk",
    title: "Rita · proteção extra",
    about: "Travas que a Rita confere antes de liberar cada operação.",
    fields: [
      { key: "max_positions_per_symbol", label: "Posições no mesmo ativo", int: true, min: 1, max: 5, hint: "Quantas operações abertas no mesmo ativo ao mesmo tempo. 1 evita dobrar a aposta no mesmo lugar." },
      { key: "max_currency_exposure", label: "Exposição máxima por moeda", int: true, min: 1, max: 10, hint: "Evita apostar tudo na mesma moeda: comprar EURUSD e GBPUSD conta −2 no dólar. Com 2, uma terceira operação contra o dólar é vetada." },
      { key: "max_spread_multiplier", label: "Spread máximo", min: 1, max: 10, suffix: "× o normal", hint: "Se o spread estiver maior que isso vezes o normal do ativo (notícia, abertura, madrugada), a Rita não deixa entrar." },
      { key: "min_lot_overrisk", label: "Tolerância do lote mínimo", min: 1, max: 5, suffix: "× o risco", hint: "Em conta pequena, o lote mínimo da corretora pode arriscar mais que o planejado. Até este múltiplo a Rita aceita; acima, veta. 1 = nunca passar do risco." },
      { key: "adaptive_risk", label: "Risco adaptativo", type: "switch", hint: "Depois de perdas seguidas a Rita reduz o risco sozinha e volta ao normal com os ganhos (a daily também usa isso)." },
    ],
  },
  {
    id: "strategist",
    agent: "strategist",
    title: "Estela · estratégias",
    about: "Como as estratégias são testadas, aprovadas e evoluídas.",
    fields: [
      {
        key: "rank_by",
        label: "Critério do ranking",
        type: "select",
        options: [
          ["win_rate", "Taxa de acerto (conservadora)"],
          ["expectancy", "Expectativa por operação"],
          ["profit_factor", "Fator de lucro"],
          ["net", "Resultado ajustado ao risco"],
        ],
        hint: "Como a Estela ordena as estratégias aprovadas. Taxa de acerto prefere quem erra pouco; expectativa prefere quem ganha mais por operação.",
      },
      { key: "min_trades", label: "Mínimo de operações no teste", int: true, min: 5, max: 1000, hint: "Uma estratégia só é aprovada com pelo menos isso de operações no histórico. Mais = mais confiável, porém menos aprovadas." },
      { key: "min_profit_factor", label: "Fator de lucro mínimo", min: 0.5, max: 5, hint: "Ganhos ÷ perdas no teste, já com spread e comissão. 1,1 = ganhar pelo menos 10% a mais do que perde." },
      { key: "oos_fraction", label: "Parte do histórico reservada para a prova", min: 0.1, max: 0.5, hint: "0,3 = os 30% mais recentes não entram na escolha e servem de prova: a estratégia precisa continuar lucrando neles." },
      { key: "ranking_interval_hours", label: "Refazer o ranking a cada", min: 0.5, max: 168, suffix: "horas", hint: "De quanto em quanto tempo a Estela retesta tudo com os candles novos." },
      { key: "evolution_enabled", label: "Evolução automática", type: "switch", hint: "A Estela testa variações das melhores estratégias (inclusive a versão scalper e a de segurar mais tempo) e só adota se melhorar no período de prova." },
      { key: "evolution_interval_hours", label: "Evoluir a cada", min: 1, max: 720, suffix: "horas", hint: "Intervalo entre as rodadas de evolução." },
    ],
  },
  {
    id: "manager",
    agent: "manager",
    title: "Gustavo · plano do dia",
    about: "Quem entra no plano e quando o gerente veta.",
    fields: [
      { key: "max_active_setups", label: "Setups ativos ao mesmo tempo", int: true, min: 1, max: 10, hint: "Quantas combinações ativo + estratégia podem gerar entradas ao mesmo tempo (no máximo uma por ativo)." },
      { key: "decision_interval_minutes", label: "Rever o plano a cada", int: true, min: 1, max: 240, suffix: "min", hint: "De quanto em quanto tempo o Gustavo reavalia quais setups ficam ativos." },
      { key: "position_review_minutes", label: "Revisar posições abertas a cada", int: true, min: 0, max: 720, suffix: "min", hint: "O Gustavo olha cada posição aberta com o mercado de agora: fecha se o motivo da entrada sumiu (evento forte chegando, notícia contra, operação parada ou devolvendo o lucro), aperta o stop para garantir lucro (nunca afrouxa) e estica ou aproxima o alvo. 0 = não revisa." },
      { key: "min_hour_quality", label: "Qualidade mínima do horário", min: 0, max: 1, hint: "O Hugo dá nota de 0 a 1 para cada hora de cada ativo (volume e movimento). Abaixo desta nota o setup fica bloqueado. 0 = opera em qualquer hora." },
      { key: "use_news_filter", label: "Usar as notícias da Nina", type: "switch", hint: "Notícias fortes definem a direção (só compra ou só venda) e vetam entradas contra elas." },
      { key: "news_block_threshold", label: "Força da notícia que veta", min: 0.1, max: 1, hint: "Quão forte (0 a 1) a notícia precisa ser para vetar uma entrada contrária. Menor = veta mais vezes." },
      { key: "ai_plan_refresh_minutes", label: "Validade do plano da IA", int: true, min: 15, max: 720, suffix: "min", hint: "Economia: se nada mudou nos candidatos, o Gustavo reaproveita o último plano da IA em vez de chamar de novo." },
    ],
  },
  {
    id: "cashier",
    agent: "cashier",
    title: "Caio · saídas",
    about: "Como as operações abertas são protegidas e encerradas.",
    fields: [
      { key: "break_even_r", label: "Zero a zero a partir de", min: 0, max: 5, suffix: "R", hint: "Quando a operação andar isso a favor (1R = a distância até o stop), o Caio move o stop para o preço de entrada. 0 desliga." },
      { key: "trailing_start_r", label: "Stop móvel a partir de", min: 0, max: 10, suffix: "R", hint: "A partir desse ganho o stop passa a seguir o preço (trailing). 0 desliga." },
      { key: "trailing_atr_mult", label: "Distância do stop móvel", min: 0.5, max: 10, suffix: "× ATR", hint: "Espaço que o stop móvel deixa, em múltiplos da volatilidade média (ATR). Maior = aguenta mais oscilação." },
      { key: "max_bars_in_trade", label: "Tempo máximo na operação", int: true, min: 0, max: 5000, suffix: "candles", hint: "Fecha a operação que não andou depois de N candles. 0 = automático: cada estilo tem o seu (o scalper sai rápido, a posição longa espera mais)." },
      { key: "adaptive_exits", label: "Gestão de saída aprendida", type: "switch", hint: "O Caio estuda as operações reais e ajusta o zero a zero e o stop móvel sozinho, dentro de limites." },
      { key: "close_before_weekend", label: "Fechar antes do fim de semana", type: "switch", hint: "Encerra as posições na sexta às 17h45 (Brasília) para não ficar exposto aos gaps de segunda." },
      { key: "b3_close_time", label: "Zerar day trade da B3 às", type: "time", hint: "Minicontratos (WIN, WDO…) são zerados nesse horário de Brasília, antes do leilão de fechamento." },
      { key: "b3_prefixes", label: "Ativos da B3 (prefixos)", type: "list", hint: "Ativos que começam com estes nomes seguem o horário da B3. Separe por vírgula." },
    ],
  },
  {
    id: "schedule",
    agent: "schedule",
    title: "Hugo · pausas por evento",
    about: "Proteção em notícias econômicas fortes (payroll, juros, inflação).",
    fields: [
      { key: "blackout_before_min", label: "Pausa antes do evento", int: true, min: 0, max: 240, suffix: "min", hint: "Nenhuma entrada nos ativos afetados nesse intervalo antes de um evento forte." },
      { key: "blackout_after_min", label: "Pausa depois do evento", int: true, min: 0, max: 240, suffix: "min", hint: "Espera o mercado se acalmar depois do evento." },
      { key: "blackout_impacts", label: "Pausar em eventos de impacto", type: "impacts", hint: "Alto = os que mais mexem no preço. Marque médio para ser ainda mais cuidadoso." },
    ],
  },
  {
    id: "news",
    agent: "news",
    title: "Nina · notícias",
    about: "Fontes de notícias e com que frequência a Nina lê e analisa.",
    extra: "feeds",
    fields: [
      { key: "news_enabled", label: "Ler notícias", type: "switch", hint: "Sem notícias, o filtro de direção do Gustavo fica neutro." },
      { key: "news_interval_minutes", label: "Ler as fontes a cada", int: true, min: 2, max: 240, suffix: "min", hint: "Leitura dos feeds (não usa IA, não custa nada)." },
      { key: "ai_news_interval_minutes", label: "Analisar com IA a cada", int: true, min: 5, max: 240, suffix: "min", hint: "A Nina junta as manchetes novas e manda para a IA de uma vez: maior = mais econômico." },
    ],
  },
  {
    id: "paper",
    agent: "infra",
    title: "Conta simulada e MT5 (avançado)",
    about: "Custos usados no simulado e nos backtests, origem dos preços e detalhes das ordens no MT5.",
    fields: [
      { key: "paper_initial_balance", label: "Saldo inicial da conta simulada", min: 100, max: 100000000, suffix: "USD", hint: "Vale ao zerar a conta simulada (Modo de operação)." },
      { key: "paper_commission_per_lot", label: "Comissão por lote (ida e volta)", min: 0, max: 500, suffix: "USD", hint: "Coloque a da sua corretora: entra no simulado e em todos os backtests." },
      { key: "paper_slippage_points", label: "Slippage", min: 0, max: 1000, suffix: "pontos", hint: "Diferença média entre o preço pedido e o executado (simulado e backtests)." },
      {
        key: "data_source",
        label: "Origem dos preços",
        type: "select",
        options: [
          ["auto", "Automático (MT5 se conectado; senão preços reais)"],
          ["real", "Preços reais grátis (Yahoo Finance e Binance)"],
          ["mt5", "Sempre o MT5"],
          ["synthetic", "Mercado simulado (sem internet)"],
        ],
        hint: "Sem corretora, a conta simulada usa preços reais do Yahoo Finance (forex, ouro, índices, B3) e da Binance (cripto). O mercado simulado é só para testes sem internet. Ao trocar entre preço simulado e real, a conta simulada recomeça e as estratégias são retestadas.",
      },
      { key: "magic_number", label: "Número mágico", int: true, min: 1, max: 2147483647, hint: "Identifica no MT5 as ordens abertas pelo META-BOT. Não mude com posições abertas." },
      { key: "deviation_points", label: "Desvio máximo da ordem", int: true, min: 0, max: 1000, suffix: "pontos", hint: "Quanto o preço pode escorregar ao enviar uma ordem a mercado no MT5." },
      { key: "server_utc_offset_hours", label: "Fuso do servidor do MT5", min: -14, max: 14, nullable: true, suffix: "horas", hint: "Vazio = detectar sozinho. Só preencha se os horários dos candles estiverem errados." },
    ],
  },
];

// ------------------------------------------------------------------ página
export function SettingsPage() {
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<any>("/api/settings") });
  const [draft, setDraft] = useState<Cfg | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const qc = useQueryClient();
  useEffect(() => {
    if (q.data && !draft) setDraft(q.data.config);
  }, [q.data, draft]);
  if (q.isLoading || !draft) return <Loading />;
  const cfg = draft;
  const defaults: Cfg = q.data.defaults;
  const set: Setter = (k, v) => {
    setDraft((d) => ({ ...(d ?? {}), [k]: v }));
    setSaved(false);
    setError(null);
  };
  const setMany = (values: Cfg) => {
    setDraft((d) => ({ ...(d ?? {}), ...values }));
    setSaved(false);
    setError(null);
  };
  const changedKeys = Object.keys(cfg).filter((k) => JSON.stringify(cfg[k]) !== JSON.stringify(q.data.config[k]) && k !== "mode" && k !== "system_running");
  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const patch: Cfg = {};
      for (const k of changedKeys) patch[k] = cfg[k];
      const res = await api.put<any>("/api/settings", patch);
      setDraft(res.config);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      setSaved(true);
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4 pb-24">
      <div>
        <h1 className="font-pixel text-sm text-gold">CONFIGURAÇÕES</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          O essencial fica aqui em cima. Os <b className="text-slate-300">ajustes finos</b> de cada agente já vêm calibrados e a equipe se ajusta sozinha na daily — só mexa se quiser. Tudo vale na hora, sem reiniciar.
        </p>
      </div>

      <ModeCard />
      <GoalsCard cfg={cfg} set={set} setMany={setMany} defaults={defaults} />
      <MarketsCard cfg={cfg} set={set} />
      <div className="grid gap-4 xl:grid-cols-[1fr_1.4fr]">
        <DailyCard cfg={cfg} set={set} />
        <AICard cfg={cfg} set={set} ai={q.data.ai} />
      </div>
      <MT5Card />

      <div className="pt-2">
        <h2 className="font-pixel text-[10px] text-gold">AJUSTES FINOS (OPCIONAL)</h2>
        <p className="mt-1 text-xs text-muted">Toque em um agente para abrir. Cada campo explica o que faz; “padrão” mostra o valor recomendado e volta para ele com um toque.</p>
      </div>
      <div className="space-y-2">
        {SECTIONS.map((s) => (
          <SectionBox key={s.id} section={s} cfg={cfg} set={set} setMany={setMany} defaults={defaults} />
        ))}
      </div>

      <SecurityCard />

      {(changedKeys.length > 0 || saved || !!error) && (
        <div className="fixed inset-x-0 bottom-[calc(4.5rem+env(safe-area-inset-bottom))] z-20 flex justify-center px-3 lg:bottom-4">
          <div className="flex max-w-full flex-wrap items-center gap-2 rounded-xl border border-line bg-panel px-3 py-2 shadow-2xl shadow-black/50">
            {error ? (
              <ErrorBox error={error} />
            ) : (
              <span className="text-sm text-muted">{saved && !changedKeys.length ? "Salvo ✓ — a equipe já está usando" : `${changedKeys.length} alteração(ões) não salva(s)`}</span>
            )}
            {changedKeys.length > 0 && (
              <Button
                variant="ghost"
                onClick={() => {
                  setDraft(q.data.config);
                  setError(null);
                }}
              >
                <Undo2 className="h-4 w-4" /> Descartar
              </Button>
            )}
            <Button onClick={save} loading={saving} disabled={!changedKeys.length}>
              <Save className="h-4 w-4" /> Salvar
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ controles
const parseNum = (t: string): number | undefined => {
  const s = t.trim().replace(",", ".");
  if (s === "" || s === "-" || s === ".") return undefined;
  const n = Number(s);
  return Number.isFinite(n) ? n : undefined;
};
const fmtNum = (v: number | null | undefined) => (v == null ? "" : String(v).replace(".", ","));

function NumInput({ value, onChange, int, nullable, suffix }: { value: number | null; onChange: (v: number | null) => void; int?: boolean; nullable?: boolean; suffix?: string }) {
  const [text, setText] = useState(fmtNum(value));
  useEffect(() => {
    if (parseNum(text) !== (value ?? undefined)) setText(fmtNum(value));
  }, [value]);
  return (
    <div className="relative">
      <Input
        inputMode={int ? "numeric" : "decimal"}
        value={text}
        className={suffix ? "pr-20" : undefined}
        onChange={(e) => {
          const t = e.target.value;
          setText(t);
          const n = parseNum(t);
          if (n !== undefined) onChange(n);
          else if (t.trim() === "" && nullable) onChange(null);
        }}
        onBlur={() => setText(fmtNum(value))}
      />
      {suffix && <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-muted">{suffix}</span>}
    </div>
  );
}

const showValue = (f: FieldDef, v: any): string => {
  if (f.type === "switch") return v ? "ligado" : "desligado";
  if (f.type === "select") return f.options?.find((o) => o[0] === v)?.[1] ?? String(v);
  if (Array.isArray(v)) return v.join(", ") || "nenhum";
  if (v == null) return "automático";
  return `${fmtNum(v)}${f.suffix ? ` ${f.suffix}` : ""}`;
};

function DefaultNote({ f, value, def, onReset }: { f: FieldDef; value: any; def: any; onReset: () => void }) {
  const differs = JSON.stringify(value) !== JSON.stringify(def);
  const range = f.min != null && f.max != null && (f.type ?? "number") === "number" ? ` · de ${fmtNum(f.min)} a ${fmtNum(f.max)}` : "";
  if (!differs) return <span className="mt-1 block text-[10px] text-slate-500">no padrão{range}</span>;
  return (
    <button type="button" onClick={onReset} className="mt-1 flex items-center gap-1 text-[10px] text-amber-300/90 hover:underline">
      <RotateCcw className="h-3 w-3" /> padrão: {showValue(f, def)}
      {range}
    </button>
  );
}

function FieldControl({ f, cfg, set, defaults }: { f: FieldDef; cfg: Cfg; set: Setter; defaults: Cfg }) {
  const v = cfg[f.key];
  const reset = () => set(f.key, defaults[f.key]);
  const type = f.type ?? "number";
  if (type === "switch")
    return (
      <div className="rounded-lg border border-line/70 bg-ink/30 p-3">
        <Switch checked={!!v} onChange={(x) => set(f.key, x)} label={<span className="font-semibold text-slate-200">{f.label}</span>} />
        <p className="mt-1.5 text-[11px] leading-snug text-muted">{f.hint}</p>
        <DefaultNote f={f} value={v} def={defaults[f.key]} onReset={reset} />
      </div>
    );
  let control: ReactNode;
  if (type === "select") control = <Select value={v} onChange={(x) => set(f.key, x)} options={f.options ?? []} />;
  else if (type === "time") control = <Input type="time" value={v ?? ""} onChange={(e) => set(f.key, e.target.value)} />;
  else if (type === "list")
    control = (
      <Input
        value={(v ?? []).join(", ")}
        onChange={(e) =>
          set(
            f.key,
            e.target.value
              .split(",")
              .map((x) => x.trim().toUpperCase())
              .filter(Boolean),
          )
        }
      />
    );
  else if (type === "impacts")
    control = (
      <div className="flex flex-wrap gap-2">
        {[
          ["High", "alto"],
          ["Medium", "médio"],
        ].map(([imp, label]) => (
          <label key={imp} className="flex items-center gap-1.5 rounded-md border border-line px-2 py-1.5 text-sm">
            <input type="checkbox" checked={(v ?? []).includes(imp)} onChange={(e) => set(f.key, e.target.checked ? [...(v ?? []), imp] : (v ?? []).filter((x: string) => x !== imp))} /> impacto {label}
          </label>
        ))}
      </div>
    );
  else control = <NumInput value={v} onChange={(x) => set(f.key, x)} int={f.int} nullable={f.nullable} suffix={f.suffix} />;
  return (
    <div className="rounded-lg border border-line/70 bg-ink/30 p-3">
      <Field label={f.label} hint={f.hint}>
        {control}
      </Field>
      <DefaultNote f={f} value={v} def={defaults[f.key]} onReset={reset} />
    </div>
  );
}

function SectionBox({ section, cfg, set, setMany, defaults }: { section: Section; cfg: Cfg; set: Setter; setMany: (v: Cfg) => void; defaults: Cfg }) {
  const [open, setOpen] = useState(false);
  const keys = section.fields.map((f) => f.key).concat(section.extra === "feeds" ? ["news_feeds"] : []);
  const off = keys.filter((k) => JSON.stringify(cfg[k]) !== JSON.stringify(defaults[k])).length;
  return (
    <section className="rounded-xl border border-line bg-panel">
      <button type="button" onClick={() => setOpen((o) => !o)} className="flex w-full items-center gap-3 px-4 py-3 text-left">
        <Avatar id={section.agent} size={34} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold text-slate-100">{section.title}</span>
          <span className="block truncate text-xs text-muted">{section.about}</span>
        </span>
        {off > 0 && <Badge tone="gold">{off} fora do padrão</Badge>}
        <ChevronDown className={clsx("h-4 w-4 shrink-0 text-muted transition", open && "rotate-180")} />
      </button>
      {open && (
        <div className="border-t border-line p-4">
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {section.fields.map((f) => (
              <FieldControl key={f.key} f={f} cfg={cfg} set={set} defaults={defaults} />
            ))}
          </div>
          {section.extra === "feeds" && <NewsFeeds feeds={cfg.news_feeds} onChange={(v) => set("news_feeds", v)} />}
          {off > 0 && (
            <Button variant="ghost" className="mt-3 text-xs" onClick={() => setMany(Object.fromEntries(keys.map((k) => [k, defaults[k]])))}>
              <RotateCcw className="h-3.5 w-3.5" /> Voltar esta seção para o padrão
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

// ------------------------------------------------------------------ cartões
function GoalsCard({ cfg, set, setMany, defaults }: { cfg: Cfg; set: Setter; setMany: (v: Cfg) => void; defaults: Cfg }) {
  const live = useLive();
  const risk = live.office.risk || {};
  const equity = Number(risk.day_start_equity || live.office.account?.equity || 0);
  const cur = risk.currency || live.office.account?.currency || "";
  const active = PROFILES.find((p) => Object.entries(p.values).every(([k, v]) => cfg[k] === v));
  const preview = (value: number, unit: string, sign: "-" | "+") => {
    if (!value) return null;
    if (unit === "money") return equity ? `= ${money((value / equity) * 100, 2)}% do patrimônio` : null;
    return equity ? `≈ ${sign}${money((equity * value) / 100)} ${cur} hoje` : null;
  };
  const unitOptions: Array<[string, string]> = [
    ["percent", "% do patrimônio"],
    ["money", `valor (${cur || "moeda da conta"})`],
  ];
  const f = (key: string) => SIMPLE_FIELDS[key];
  return (
    <Card title="Metas do dia e risco">
      <div className="mb-4">
        <div className="mb-2 flex items-center justify-between gap-2">
          <span className="text-xs font-semibold text-slate-300">Perfil rápido</span>
          <span className="text-[11px] text-muted">{active ? `usando: ${active.label}` : "personalizado"}</span>
        </div>
        <div className="grid gap-2 sm:grid-cols-3">
          {PROFILES.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => setMany(p.values)}
              className={clsx("rounded-lg border p-3 text-left transition", active?.key === p.key ? "border-gold bg-gold/10" : "border-line hover:bg-panel2")}
            >
              <div className={clsx("text-sm font-semibold", active?.key === p.key ? "text-gold" : "text-slate-100")}>{p.label}</div>
              <div className="mt-0.5 text-[11px] leading-snug text-muted">{p.desc}</div>
            </button>
          ))}
        </div>
        <p className="mt-2 text-[11px] text-muted">O perfil só preenche os campos abaixo — confira e toque em Salvar.</p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="rounded-lg border border-red-500/20 bg-red-500/5 p-3">
          <Field label="⛔ Limite de perda do dia" hint="Chegou nessa perda, a Rita para a equipe até amanhã (nada de tentar recuperar no mesmo dia). 0 = sem limite.">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <NumInput value={cfg.daily_loss_limit} onChange={(v) => set("daily_loss_limit", v ?? 0)} />
              </div>
              <Select value={cfg.daily_loss_unit} onChange={(v) => set("daily_loss_unit", v)} options={unitOptions} className="w-40" />
            </div>
          </Field>
          <span className="mt-1 block text-[11px] text-red-200/80">{preview(cfg.daily_loss_limit, cfg.daily_loss_unit, "-") ?? (cfg.daily_loss_limit ? "" : "sem limite de perda diário")}</span>
        </div>
        <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/5 p-3">
          <Field label="🎯 Meta de ganho do dia" hint="Bateu a meta, a equipe encerra o dia no lucro e só volta amanhã. 0 = sem meta (opera o dia todo).">
            <div className="flex gap-2">
              <div className="min-w-0 flex-1">
                <NumInput value={cfg.daily_profit_target} onChange={(v) => set("daily_profit_target", v ?? 0)} />
              </div>
              <Select value={cfg.daily_profit_unit} onChange={(v) => set("daily_profit_unit", v)} options={unitOptions} className="w-40" />
            </div>
          </Field>
          <span className="mt-1 block text-[11px] text-emerald-200/80">{preview(cfg.daily_profit_target, cfg.daily_profit_unit, "+") ?? (cfg.daily_profit_target ? "" : "sem meta: opera o dia todo")}</span>
        </div>
        <div className="md:col-span-2">
          <FieldControl f={f("close_on_daily_limit")} cfg={cfg} set={set} defaults={defaults} />
        </div>
        <div>
          <FieldControl f={f("risk_per_trade_pct")} cfg={cfg} set={set} defaults={defaults} />
          {equity > 0 && <span className="mt-1 block text-[11px] text-muted">≈ {money((equity * (cfg.risk_per_trade_pct || 0)) / 100)} {cur} por operação, se bater no stop</span>}
        </div>
        <FieldControl f={f("max_open_positions")} cfg={cfg} set={set} defaults={defaults} />
        <FieldControl f={f("max_drawdown_pct")} cfg={cfg} set={set} defaults={defaults} />
      </div>
    </Card>
  );
}

const SIMPLE_FIELDS: Record<string, FieldDef> = {
  close_on_daily_limit: {
    key: "close_on_daily_limit",
    label: "Encerrar as posições abertas ao bater a meta ou o limite",
    type: "switch",
    hint: "Ligado: o Caio fecha tudo na hora e o resultado do dia fica garantido. Desligado: as abertas seguem até o stop ou o alvo, mas nenhuma nova entra.",
  },
  risk_per_trade_pct: { key: "risk_per_trade_pct", label: "Risco por operação", min: 0.05, max: 5, suffix: "%", hint: "Quanto do patrimônio cada operação pode perder se bater no stop. O lote é calculado a partir disso." },
  max_open_positions: { key: "max_open_positions", label: "Posições abertas ao mesmo tempo", int: true, min: 1, max: 20, hint: "Limite de operações abertas juntas, somando todos os ativos." },
  max_drawdown_pct: { key: "max_drawdown_pct", label: "Trava geral: queda máxima desde o pico", min: 2, max: 50, suffix: "%", hint: "Se o patrimônio cair isso desde o maior valor já atingido, tudo para até você liberar (pede senha)." },
  daily_meeting_enabled: { key: "daily_meeting_enabled", label: "Daily automática", type: "switch", hint: "A equipe se reúne todo dia no horário abaixo (se trabalhou no dia), escreve o relatório e aplica os aprendizados no dia seguinte." },
  daily_meeting_time: { key: "daily_meeting_time", label: "Horário da daily (Brasília)", type: "time", hint: "Depois do fechamento dos mercados do dia é o ideal." },
  daily_break_minutes: { key: "daily_break_minutes", label: "Pausa depois da daily", int: true, min: 0, max: 720, suffix: "min", hint: "Depois da daily do horário, o escritório fecha por esse tempo (ninguém abre posição nova; o Caio continua protegendo as abertas) e reabre sozinho. 0 = sem pausa." },
  ai_enabled: { key: "ai_enabled", label: "IA ligada", type: "switch", hint: "Desligada, os agentes seguem só com as regras (funciona igual, sem custo)." },
  ai_daily_budget_usd: { key: "ai_daily_budget_usd", label: "Orçamento diário", min: 0, max: 1000, suffix: "US$/dia", hint: "Teto de gasto com IA por dia. Passou, os agentes seguem só com as regras até amanhã. 0 = sem teto." },
  ai_max_calls_per_hour: { key: "ai_max_calls_per_hour", label: "Máximo de chamadas por hora", int: true, min: 0, max: 500, hint: "Segurança extra contra gasto inesperado." },
};

function MarketsCard({ cfg, set }: { cfg: Cfg; set: Setter }) {
  const catalog = useQuery({ queryKey: ["catalog"], queryFn: () => api.get<any>("/api/strategies") });
  return (
    <Card title="O que a equipe opera">
      <div className="grid gap-4 lg:grid-cols-2">
        <Field label="Ativos (nome exato na sua corretora)" hint="Ex.: EURUSD, XAUUSD, US500, BTCUSD, WIN$N. Cada corretora tem sufixos próprios (EURUSDm, EURUSD.a…): confira no MT5.">
          <WatchlistEditor value={cfg.watchlist} onChange={(v) => set("watchlist", v)} />
        </Field>
        <Field label="Tempos gráficos" hint="A Estela testa as estratégias em cada um. M5 = operações de minutos (scalper); H4/D1 = segura por dias. A equipe compara os estilos e passa a preferir o que dá mais resultado.">
          <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
            {Object.entries(TIMEFRAME_INFO).map(([tf, info]) => {
              const on = cfg.timeframes.includes(tf);
              return (
                <label key={tf} className={clsx("flex cursor-pointer items-start gap-2 rounded-lg border px-2 py-1.5 text-sm", on ? "border-gold/60 bg-gold/10" : "border-line")}>
                  <input type="checkbox" className="mt-1" checked={on} onChange={(e) => set("timeframes", e.target.checked ? [...cfg.timeframes, tf] : cfg.timeframes.filter((x: string) => x !== tf))} />
                  <span>
                    <b>{tf}</b>
                    <span className="block text-[10px] leading-tight text-muted">{info}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </Field>
      </div>
      <details className="mt-4">
        <summary className="cursor-pointer text-xs text-gold">
          Estratégias ligadas: {cfg.enabled_strategies.length ? `${cfg.enabled_strategies.length} escolhidas` : "todas (recomendado)"}
        </summary>
        <p className="mt-2 text-[11px] text-muted">Nenhuma marcada = todas. O normal é deixar a Estela testar todas e aprovar só as que funcionam em cada ativo.</p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {catalog.data?.strategies.map((s: any) => {
            const on = cfg.enabled_strategies.includes(s.key);
            return (
              <button
                key={s.key}
                type="button"
                title={s.description}
                onClick={() => set("enabled_strategies", on ? cfg.enabled_strategies.filter((k: string) => k !== s.key) : [...cfg.enabled_strategies, s.key])}
                className={clsx("rounded-md border px-2 py-1 text-xs", on ? "border-gold bg-gold/15 text-gold" : "border-line text-slate-300")}
              >
                {s.name}
              </button>
            );
          })}
        </div>
      </details>
    </Card>
  );
}

function DailyCard({ cfg, set }: { cfg: Cfg; set: Setter }) {
  const defaults = useQuery({ queryKey: ["settings"], queryFn: () => api.get<any>("/api/settings") }).data?.defaults ?? {};
  return (
    <Card title="Daily da equipe">
      <div className="space-y-3">
        <FieldControl f={SIMPLE_FIELDS.daily_meeting_enabled} cfg={cfg} set={set} defaults={defaults} />
        <FieldControl f={SIMPLE_FIELDS.daily_meeting_time} cfg={cfg} set={set} defaults={defaults} />
        <FieldControl f={SIMPLE_FIELDS.daily_break_minutes} cfg={cfg} set={set} defaults={defaults} />
        <p className="text-[11px] leading-snug text-muted">
          Na daily cada agente avalia a sua parte e decidem ajustes seguros para amanhã: horários a evitar, estratégias que ficam fora do plano até passarem na revalidação, preferência entre scalper e posição longa e o risco da Rita (que só diminui ou volta ao normal, nunca aumenta sozinho).
        </p>
      </div>
    </Card>
  );
}

function AICard({ cfg, set, ai }: { cfg: Cfg; set: Setter; ai: any }) {
  const [prompt, setPrompt] = useState<null | { remove: boolean }>(null);
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const qc = useQueryClient();
  const defaults = useQuery({ queryKey: ["settings"], queryFn: () => api.get<any>("/api/settings") }).data?.defaults ?? {};
  const models: Array<[string, any]> = Object.entries(ai.models || {});
  const last = ai.last;
  return (
    <Card title="Inteligência artificial · OpenRouter">
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        {ai.available ? <Badge tone="green">IA ligada</Badge> : ai.key_set ? <Badge tone="gold">IA desligada nas configurações</Badge> : <Badge tone="red">sem chave: os agentes usam só as regras</Badge>}
        <span className="text-xs text-muted">
          gasto hoje US$ {money(ai.usage?.today_usd, 4)} · 30 dias US$ {money(ai.usage?.last_30d_usd, 3)}
        </span>
      </div>
      <div className="rounded-lg border border-line bg-ink/40 p-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-slate-200">Chave do OpenRouter</span>
          {ai.key_set ? <Badge tone="green">{ai.key_masked}</Badge> : <Badge>sem chave</Badge>}
          {ai.from_env && <Badge tone="blue">vinda da stack (MB_OPENROUTER_API_KEY)</Badge>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Input type="password" placeholder="sk-or-v1-…" value={key} onChange={(e) => setKey(e.target.value)} className="min-w-0 flex-1" autoComplete="off" />
          <Button onClick={() => setPrompt({ remove: false })} disabled={!key}>
            Salvar
          </Button>
          {ai.key_set && !ai.from_env && (
            <Button variant="ghost" onClick={() => setPrompt({ remove: true })}>
              Remover
            </Button>
          )}
        </div>
        <p className="mt-2 text-[11px] leading-snug text-muted">
          Crie em{" "}
          <a className="text-gold underline" href="https://openrouter.ai/keys" target="_blank" rel="noreferrer">
            openrouter.ai/keys
          </a>{" "}
          e coloque alguns dólares de crédito. A chave é conferida (sem gastar) e fica criptografada no banco.
        </p>
        {msg && <p className="mt-2 text-sm text-emerald-300">{msg}</p>}
        {last && !last.ok && last.error && (
          <p className="mt-2 text-xs text-red-300">
            Última chamada falhou{last.at ? ` (${dateTime(last.at)})` : ""}: {last.error}
          </p>
        )}
      </div>

      <div className="mt-4">
        <h3 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-slate-200">
          <Lock className="h-3.5 w-3.5 text-muted" /> Modelo de cada agente
        </h3>
        <p className="mb-2 text-[11px] text-muted">Definidos pelo sistema para economizar (a equipe fica ligada o dia todo). Não precisa escolher nada.</p>
        <div className="divide-y divide-line rounded-lg border border-line">
          {models.map(([tier, m]) => (
            <div key={tier} className="flex items-start gap-3 px-3 py-2">
              <Avatar id={m.agent} size={30} className="mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-x-2 text-sm">
                  <b className="text-slate-100">{tier === "daily" ? "Daily (Aurora escreve)" : tier === "news" ? "Nina · notícias" : "Gustavo · plano"}</b>
                  <code className="text-[11px] text-sky">{m.model}</code>
                </div>
                <div className="text-[11px] leading-snug text-muted">{m.why}</div>
              </div>
              {Array.isArray(m.price) && <span className="shrink-0 text-right text-[10px] leading-tight text-muted">US$ {fmtNum(m.price[0])} / {fmtNum(m.price[1])}<br />por milhão</span>}
            </div>
          ))}
        </div>
        {ai.fallback && <p className="mt-1.5 text-[11px] text-muted">Reserva automática se um modelo falhar: {ai.fallback}.</p>}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <FieldControl f={SIMPLE_FIELDS.ai_enabled} cfg={cfg} set={set} defaults={defaults} />
        <FieldControl f={SIMPLE_FIELDS.ai_daily_budget_usd} cfg={cfg} set={set} defaults={defaults} />
        <FieldControl f={SIMPLE_FIELDS.ai_max_calls_per_hour} cfg={cfg} set={set} defaults={defaults} />
      </div>
      {ai.usage?.by_agent?.length > 0 && (
        <div className="mt-3 text-xs text-muted">
          Últimos 7 dias:{" "}
          {ai.usage.by_agent.map((u: any) => (
            <span key={u.agent} className="mr-3 inline-block">
              {u.agent}: {u.calls} chamadas · US$ {money(u.cost_usd, 3)}
            </span>
          ))}
        </div>
      )}
      <PasswordPrompt
        open={prompt !== null}
        title={prompt?.remove ? "Remover a chave" : "Salvar a chave do OpenRouter"}
        description={prompt?.remove ? "Sem a chave, os agentes seguem só com as regras." : "A chave é conferida no OpenRouter (sem gastar) e fica criptografada no banco."}
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          if (!prompt) return;
          const r = await api.post<any>("/api/settings/ai-key", { api_key: prompt.remove ? null : key, password });
          setMsg(r.message);
          setKey("");
          patchSystem({ ai: !!r.status?.available });
          setPrompt(null);
          void qc.invalidateQueries({ queryKey: ["settings"] });
        }}
      />
    </Card>
  );
}

function WatchlistEditor({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState("");
  const search = useQuery({ queryKey: ["symbols", text], queryFn: () => api.get<any[]>(`/api/market/symbols?q=${encodeURIComponent(text)}`), enabled: text.length >= 2 });
  const add = (s: string) => {
    const sym = s.trim();
    if (sym && !value.includes(sym)) onChange([...value, sym]);
    setText("");
  };
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-1.5">
        {value.map((s) => (
          <Badge key={s} tone="blue" className="text-xs">
            {s}
            <button type="button" onClick={() => onChange(value.filter((x) => x !== s))} className="ml-1 text-sky-200 hover:text-white" aria-label={`Remover ${s}`}>
              ×
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex gap-2">
        <Input value={text} onChange={(e) => setText(e.target.value.toUpperCase())} list="wl-symbols" placeholder="Buscar ou digitar o ativo" onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add(text))} />
        <datalist id="wl-symbols">
          {search.data?.map((s) => (
            <option key={s.name} value={s.name}>
              {s.description}
            </option>
          ))}
        </datalist>
        <Button type="button" variant="ghost" onClick={() => add(text)} aria-label="Adicionar ativo">
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

function ModeCard() {
  const live = useLive();
  const [prompt, setPrompt] = useState<null | "live" | "kill" | "paper-reset">(null);
  const risk = live.office.risk || {};
  const mode = live.system.mode || "paper";
  return (
    <Card title="Modo de operação">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={mode === "live" ? "gold" : "blue"} className="text-sm">
          {mode === "live" ? "Conta da corretora (MT5)" : "Conta simulada"}
        </Badge>
        {mode === "paper" ? (
          <Button variant="primary" onClick={() => setPrompt("live")}>
            Usar a conta do MT5
          </Button>
        ) : (
          <Button
            variant="ghost"
            onClick={async () => {
              const r = await api.post<any>("/api/system/mode", { mode: "paper" });
              patchSystem({ mode: r.mode });
            }}
          >
            Voltar para o simulado
          </Button>
        )}
        <Button variant="ghost" onClick={() => setPrompt("paper-reset")}>
          Zerar conta simulada
        </Button>
        {risk.kill_switch && (
          <Button variant="danger" onClick={() => setPrompt("kill")}>
            Liberar trava geral
          </Button>
        )}
      </div>
      <p className="mt-3 text-xs text-muted">
        Na <b>conta simulada</b> a equipe opera com <b>preços reais</b> (do MT5 se estiver ligado; senão, grátis do Yahoo Finance e da Binance), mas sem enviar ordens: é como uma conta de verdade, sem risco e sem precisar de corretora. Na conta do MT5 as ordens vão para a conta logada no terminal: comece por uma <b>conta demo</b>.
      </p>
      <PasswordPrompt
        open={prompt === "live"}
        title="Operar na conta do MT5"
        description={<p>As ordens passarão a ser enviadas à corretora logada no MetaTrader 5. Recomendo começar com uma conta demo. Confirme com sua senha.</p>}
        confirmLabel="Entendi, usar a conta"
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          const r = await api.post<any>("/api/system/mode", { mode: "live", password, confirm: true });
          patchSystem({ mode: r.mode, data_source: "mt5" });
          setPrompt(null);
        }}
      />
      <PasswordPrompt
        open={prompt === "kill"}
        title="Liberar a trava geral"
        description={<p>A trava foi acionada porque a queda desde o pico passou do limite ({risk.kill_reason}). Liberar permite novas entradas.</p>}
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          await api.post("/api/system/kill-switch/reset", { password });
          setPrompt(null);
        }}
      />
      <PasswordPrompt
        open={prompt === "paper-reset"}
        title="Zerar a conta simulada"
        description={<p>O saldo simulado volta ao valor inicial (o histórico continua guardado).</p>}
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          await api.post("/api/system/paper/reset", { password });
          setPrompt(null);
        }}
      />
    </Card>
  );
}

interface TerminalRow {
  id: number;
  name: string;
  bridge_url: string;
  token_set: boolean;
  login: string;
  server: string;
  broker_password_set: boolean;
  active: boolean;
}

function MT5Card() {
  const live = useLive();
  const q = useQuery({ queryKey: ["terminals"], queryFn: () => api.get<TerminalRow[]>("/api/settings/terminals") });
  const [edit, setEdit] = useState<(Partial<TerminalRow> & { token?: string; broker_password?: string }) | null>(null);
  const [test, setTest] = useState<any>(null);
  const [prompt, setPrompt] = useState(false);
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<any>("/api/settings") });
  const panelUrl: string = settings.data?.mt5?.panel_url || "";
  const mt5 = live.system.mt5 || {};
  return (
    <Card
      title="MetaTrader 5 · qualquer corretora"
      actions={
        panelUrl ? (
          <a href={panelUrl} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-xs text-sky hover:underline">
            abrir o terminal <ExternalLink className="h-3 w-3" />
          </a>
        ) : undefined
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <Badge tone={mt5.connected ? "green" : mt5.configured ? "red" : "slate"}>{mt5.connected ? "conectado" : mt5.configured ? "desconectado" : "não configurado"}</Badge>
        {mt5.connected && (
          <span className="text-slate-300">
            conta {mt5.login} · {mt5.server} · {mt5.company} · saldo {money(mt5.balance)} {mt5.currency} · fuso UTC{mt5.offset_hours >= 0 ? "+" : ""}
            {mt5.offset_hours}
          </span>
        )}
        {!mt5.connected && mt5.message && <span className="text-muted">{mt5.message}</span>}
      </div>
      <div className="space-y-2">
        {q.data?.map((t) => (
          <div key={t.id} className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel2 px-3 py-2 text-sm">
            <b>{t.name}</b>
            {t.active && <Badge tone="green">ativo</Badge>}
            <span className="text-xs text-muted">{t.bridge_url}</span>
            <span className="text-xs text-muted">{t.login ? `conta ${t.login} · ${t.server}` : "login feito pela tela do MT5"}</span>
            {!t.token_set && <Badge tone="red">sem token</Badge>}
            <div className="ml-auto flex gap-1">
              <Button variant="ghost" className="px-2 py-1 text-xs" onClick={() => setEdit({ ...t, token: "", broker_password: "" })}>
                Editar
              </Button>
              <Button variant="ghost" className="px-2 py-1 text-xs" onClick={async () => setTest(await api.post("/api/settings/terminals/test", { bridge_url: t.bridge_url, terminal_id: t.id }))}>
                Testar
              </Button>
            </div>
          </div>
        ))}
        {!q.data?.length && <p className="text-sm text-muted">Nenhum MetaTrader 5 ligado ainda: a conta simulada usa preços reais grátis (Yahoo Finance e Binance).</p>}
        <Button variant="subtle" className="text-xs" onClick={() => setEdit({ name: q.data?.length ? "MT5 corretora 2" : "MT5 principal", bridge_url: "http://", active: !q.data?.length })}>
          <Plus className="h-3.5 w-3.5" /> {q.data?.length ? "Adicionar terminal (outra corretora)" : "Ligar um MetaTrader 5"}
        </Button>
        {test && <div className={`rounded-lg px-3 py-2 text-sm ${test.ok ? "bg-emerald-500/10 text-emerald-200" : "bg-red-500/10 text-red-200"}`}>{test.message}</div>}
      </div>
      <details className="mt-4 text-sm text-slate-300">
        <summary className="cursor-pointer text-gold">Como ligar um MetaTrader 5 (PC ou VPS Windows)</summary>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs leading-relaxed text-muted">
          <li>
            Numa máquina Windows sempre ligada, instale o MT5 da sua corretora, entre na conta (comece pela <b>demo</b>) e ligue o <b>Algo Trading</b> (botão verde na barra; em <i>Ferramentas → Opções → Expert Advisors</i>, marque "Permitir negociação algorítmica").
          </li>
          <li>
            Na mesma máquina, rode o bridge do Meta-Bot com o <b>mesmo token</b> da stack (<code>MB_MT5_BRIDGE_TOKEN</code>): baixe o{" "}
            <a className="text-sky underline" href="https://github.com/dhqdev/meta-bot/tree/main/mt5/windows" target="_blank" rel="noreferrer">
              kit para Windows
            </a>{" "}
            e execute o <code>iniciar-bridge.bat</code>.
          </li>
          <li>
            Ligue a máquina ao servidor com o <b>Tailscale</b> (grátis) e cadastre aqui o endereço <code>http://IP-DO-TAILSCALE:8001</code> com o token. Clique em <i>Testar</i>.
          </li>
          <li>Com o MT5 ligado, a equipe passa a usar os preços da corretora. Ordens de verdade só depois de clicar em "Usar a conta do MT5", em Modo de operação (pede senha).</li>
          <li>Várias corretoras: um MT5 + um bridge por conta (portas 8001, 8002…), cada um cadastrado aqui; o ativo é o que o sistema usa.</li>
        </ol>
      </details>
      {edit && (
        <div className="mt-4 space-y-3 rounded-lg border border-line p-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Nome">
              <Input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
            </Field>
            <Field label="Endereço do bridge" hint="Ex.: http://100.101.102.103:8001 (IP do Tailscale da máquina do MT5)">
              <Input value={edit.bridge_url ?? ""} onChange={(e) => setEdit({ ...edit, bridge_url: e.target.value })} />
            </Field>
            <Field label="Token do bridge" hint={edit.token_set ? "Deixe em branco para manter o atual." : "O mesmo token configurado no bridge (MB_MT5_BRIDGE_TOKEN da stack)."}>
              <Input type="password" value={edit.token ?? ""} onChange={(e) => setEdit({ ...edit, token: e.target.value })} />
            </Field>
            <Field label="Conta (número)" hint="Opcional: login automático.">
              <Input value={edit.login ?? ""} onChange={(e) => setEdit({ ...edit, login: e.target.value })} />
            </Field>
            <Field label="Servidor" hint="Exatamente como no MT5, ex.: XPMT5-DEMO">
              <Input value={edit.server ?? ""} onChange={(e) => setEdit({ ...edit, server: e.target.value })} />
            </Field>
            <Field label="Senha da conta na corretora" hint={edit.broker_password_set ? "Deixe em branco para manter." : "Criptografada no banco."}>
              <Input type="password" value={edit.broker_password ?? ""} onChange={(e) => setEdit({ ...edit, broker_password: e.target.value })} />
            </Field>
          </div>
          <Switch checked={!!edit.active} onChange={(v) => setEdit({ ...edit, active: v })} label="Usar este terminal" />
          <div className="flex justify-between">
            {edit.id ? (
              <Button
                variant="ghost"
                className="text-down"
                onClick={async () => {
                  const password = window.prompt("Confirme com sua senha para remover o terminal");
                  if (!password) return;
                  await api.del(`/api/settings/terminals/${edit.id}`, { password });
                  setEdit(null);
                  void q.refetch();
                }}
              >
                <Trash2 className="h-4 w-4" /> Remover
              </Button>
            ) : (
              <span />
            )}
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setEdit(null)}>
                Cancelar
              </Button>
              <Button onClick={() => setPrompt(true)}>Salvar terminal</Button>
            </div>
          </div>
        </div>
      )}
      <PasswordPrompt
        open={prompt}
        title="Salvar terminal"
        description="Os dados de acesso ficam criptografados no banco."
        onCancel={() => setPrompt(false)}
        onConfirm={async (password) => {
          const body = { name: edit?.name, bridge_url: edit?.bridge_url, token: edit?.token || null, login: edit?.login || null, server: edit?.server || null, broker_password: edit?.broker_password || null, active: !!edit?.active, password };
          if (edit?.id) await api.put(`/api/settings/terminals/${edit.id}`, body);
          else await api.post("/api/settings/terminals", body);
          setPrompt(false);
          setEdit(null);
          void q.refetch();
        }}
      />
    </Card>
  );
}

function NewsFeeds({ feeds, onChange }: { feeds: any[]; onChange: (v: any[]) => void }) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  return (
    <div className="mt-4 rounded-lg border border-line/70 bg-ink/30 p-3">
      <h4 className="text-sm font-semibold text-slate-200">Fontes de notícias</h4>
      <p className="mb-2 text-[11px] text-muted">Feeds RSS em inglês e português. Desligue as que não quiser.</p>
      <div className="space-y-1.5">
        {feeds.map((f, i) => (
          <div key={f.url} className="flex items-center gap-2 text-sm">
            <Switch checked={f.enabled} onChange={(v) => onChange(feeds.map((x, j) => (j === i ? { ...x, enabled: v } : x)))} />
            <b className="w-36 shrink-0 truncate sm:w-44">{f.name}</b>
            <span className="min-w-0 flex-1 truncate text-xs text-muted">{f.url}</span>
            <Badge>{f.lang}</Badge>
            <button className="text-muted hover:text-down" onClick={() => onChange(feeds.filter((_, j) => j !== i))} aria-label={`Remover ${f.name}`}>
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Input placeholder="Nome" value={name} onChange={(e) => setName(e.target.value)} className="w-40" />
        <Input placeholder="https://…/rss" value={url} onChange={(e) => setUrl(e.target.value)} className="min-w-52 flex-1" />
        <Button
          variant="ghost"
          onClick={() => {
            if (!name || !url.startsWith("http")) return;
            onChange([...feeds, { name, url, lang: /\.br\b|br\./.test(url) ? "pt" : "en", enabled: true }]);
            setName("");
            setUrl("");
          }}
        >
          <Plus className="h-4 w-4" /> Adicionar
        </Button>
      </div>
    </div>
  );
}

function SecurityCard() {
  const { status, refresh } = useAuth();
  const [pw, setPw] = useState({ current: "", next: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [totp, setTotp] = useState<{ secret: string; uri: string; qr: string } | null>(null);
  const [code, setCode] = useState("");
  const [prompt, setPrompt] = useState<null | "setup">(null);
  return (
    <Card title="Segurança">
      <div className="grid gap-6 md:grid-cols-2">
        <form
          className="space-y-2"
          onSubmit={async (e) => {
            e.preventDefault();
            setError(null);
            try {
              await api.post("/api/auth/password", { current_password: pw.current, new_password: pw.next });
              setMsg("Senha alterada. As outras sessões foram encerradas.");
              setPw({ current: "", next: "" });
            } catch (err) {
              setError(err);
            }
          }}
        >
          <h4 className="text-sm font-semibold">Trocar a senha</h4>
          <Input type="password" placeholder="Senha atual" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} autoComplete="current-password" />
          <Input type="password" placeholder="Nova senha" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} autoComplete="new-password" />
          <Button type="submit" variant="ghost" disabled={!pw.current || !pw.next}>
            Trocar
          </Button>
          {msg && <p className="text-sm text-emerald-300">{msg}</p>}
          <ErrorBox error={error} />
        </form>
        <div className="space-y-2">
          <h4 className="text-sm font-semibold">Verificação em duas etapas</h4>
          {status?.totp_enabled ? (
            <Badge tone="green">ativa</Badge>
          ) : totp ? (
            <div className="space-y-2">
              <img src={totp.qr} alt="QR code" className="h-40 w-40 rounded bg-white p-2" />
              <p className="break-all text-xs text-muted">Ou digite no app: {totp.secret}</p>
              <div className="flex gap-2">
                <Input inputMode="numeric" placeholder="Código de 6 dígitos" value={code} onChange={(e) => setCode(e.target.value)} className="max-w-40" />
                <Button
                  onClick={async () => {
                    try {
                      await api.post("/api/auth/2fa/enable", { code });
                      setTotp(null);
                      await refresh();
                    } catch (err) {
                      setError(err);
                    }
                  }}
                >
                  Ativar
                </Button>
              </div>
            </div>
          ) : (
            <Button variant="ghost" onClick={() => setPrompt("setup")}>
              Configurar com app autenticador
            </Button>
          )}
        </div>
      </div>
      <PasswordPrompt
        open={prompt === "setup"}
        title="Verificação em duas etapas"
        description="Confirme sua senha para gerar o QR code."
        onCancel={() => setPrompt(null)}
        onConfirm={async (password) => {
          const r = await api.post<{ secret: string; uri: string }>("/api/auth/2fa/setup", { password });
          setTotp({ ...r, qr: await QRCode.toDataURL(r.uri, { margin: 1, width: 220 }) });
          setPrompt(null);
        }}
      />
    </Card>
  );
}
