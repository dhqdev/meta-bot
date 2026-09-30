import { Maximize2 } from "lucide-react";
import { useEffect, useRef } from "react";
import { onLiveEvent, useLive } from "../lib/live";
import { saoPauloHour } from "../lib/format";
import { OFFICE_SIZE, OfficeEngine } from "./engine";

/** O escritório visto de cima, com os agentes trabalhando em tempo real. */
export function OfficeCanvas({ selected, onSelect }: { selected?: string; onSelect: (id?: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<OfficeEngine | null>(null);
  const scaleRef = useRef(2);
  const live = useLive();

  if (!engineRef.current) engineRef.current = new OfficeEngine();
  const engine = engineRef.current;

  // estado recebido pelo WebSocket → decoração do escritório
  useEffect(() => {
    engine.syncAgents(Object.values(live.agents));
  }, [live.agents, engine]);

  useEffect(() => {
    const o = live.office;
    const acc = o.account || {};
    engine.info = {
      ...engine.info,
      running: !!live.system.running,
      mode: live.system.mode || "paper",
      source: live.system.data_source || "synthetic",
      mt5: live.system.mt5 || {},
      headline: o.headline,
      newsMood: o.news_mood?.value,
      plan: o.plan || [],
      equity: o.equity || [],
      balance: acc.equity ?? acc.balance,
      currency: acc.currency,
      killSwitch: !!o.risk?.kill_switch,
      dayBlocked: !!o.risk?.day_blocked,
      sessions: o.sessions || [],
    };
  }, [live.office, live.system, engine]);

  useEffect(() => onLiveEvent((ev) => engine.handle(ev)), [engine]);

  useEffect(() => {
    engine.selected = selected;
  }, [selected, engine]);

  // laço de animação + escala nítida
  useEffect(() => {
    const canvas = canvasRef.current!;
    const wrap = wrapRef.current!;
    const ctx = canvas.getContext("2d")!;
    let raf = 0;
    let last = performance.now();
    let hourTick = 0;
    const resize = () => {
      const dpr = window.devicePixelRatio || 1;
      const w = wrap.clientWidth;
      const scale = Math.max(1, Math.ceil((w * dpr) / OFFICE_SIZE.width));
      scaleRef.current = scale;
      canvas.width = OFFICE_SIZE.width * scale;
      canvas.height = OFFICE_SIZE.height * scale;
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    resize();
    const loop = (now: number) => {
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      if (now - hourTick > 20000 || hourTick === 0) {
        engine.info.hour = saoPauloHour();
        hourTick = now;
      }
      engine.update(dt);
      engine.render(ctx, scaleRef.current);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [engine]);

  const toLogical = (e: React.MouseEvent) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return [((e.clientX - rect.left) / rect.width) * OFFICE_SIZE.width, ((e.clientY - rect.top) / rect.height) * OFFICE_SIZE.height] as const;
  };

  return (
    <div ref={wrapRef} className="relative w-full overflow-hidden rounded-xl border border-line bg-black">
      <canvas
        ref={canvasRef}
        className="pixelated block h-auto w-full cursor-default"
        style={{ aspectRatio: `${OFFICE_SIZE.width} / ${OFFICE_SIZE.height}` }}
        onMouseMove={(e) => {
          const [x, y] = toLogical(e);
          const hit = engine.hitTest(x, y);
          engine.hovered = hit;
          e.currentTarget.style.cursor = hit ? "pointer" : "default";
        }}
        onMouseLeave={() => (engine.hovered = undefined)}
        onClick={(e) => {
          const [x, y] = toLogical(e);
          onSelect(engine.hitTest(x, y));
        }}
      />
      <button
        type="button"
        title="Tela cheia"
        onClick={() => (document.fullscreenElement ? document.exitFullscreen() : wrapRef.current?.requestFullscreen?.())}
        className="absolute right-2 bottom-2 rounded-md bg-black/50 p-1.5 text-white/80 hover:bg-black/70"
      >
        <Maximize2 className="h-4 w-4" />
      </button>
    </div>
  );
}
