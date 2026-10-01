import { Maximize2, Minimize2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { onLiveEvent, useLive } from "../lib/live";
import { saoPauloHour } from "../lib/format";
import { OFFICE_SIZE, OfficeEngine } from "./engine";

/** O escritório visto de cima, com os agentes trabalhando em tempo real. */
export function OfficeCanvas({ selected, onSelect }: { selected?: string; onSelect: (id?: string) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<OfficeEngine | null>(null);
  const [full, setFull] = useState(false);
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

  useEffect(() => {
    const onFs = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  // laço de animação: mundo em escala inteira (canvas à parte) + sobreposição na resolução da tela
  useEffect(() => {
    const canvas = canvasRef.current!;
    const wrap = wrapRef.current!;
    const ctx = canvas.getContext("2d")!;
    const world = document.createElement("canvas");
    const wctx = world.getContext("2d")!;
    let raf = 0;
    let last = performance.now();
    let hourTick = 0;
    let k = 2;
    let dpr = 1;
    const resize = () => {
      dpr = window.devicePixelRatio || 1;
      const rect = wrap.getBoundingClientRect();
      const byHeight = document.fullscreenElement ? (window.innerHeight * OFFICE_SIZE.width) / OFFICE_SIZE.height : Infinity;
      const cssW = Math.max(160, Math.min(rect.width, byHeight));
      const cssH = (cssW * OFFICE_SIZE.height) / OFFICE_SIZE.width;
      canvas.style.width = `${cssW}px`;
      canvas.style.height = `${cssH}px`;
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      k = Math.max(1, Math.min(4, Math.ceil(canvas.width / OFFICE_SIZE.width)));
      world.width = OFFICE_SIZE.width * k;
      world.height = OFFICE_SIZE.height * k;
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    window.addEventListener("resize", resize);
    resize();
    const loop = (nowMs: number) => {
      const dt = Math.min(0.1, (nowMs - last) / 1000);
      last = nowMs;
      if (nowMs - hourTick > 20000 || hourTick === 0) {
        engine.info.hour = saoPauloHour();
        hourTick = nowMs;
      }
      engine.update(dt);
      engine.renderWorld(wctx, k);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.imageSmoothingEnabled = canvas.width !== world.width;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(world, 0, 0, canvas.width, canvas.height);
      engine.renderOverlay(ctx, canvas.width / OFFICE_SIZE.width, dpr);
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("resize", resize);
    };
  }, [engine]);

  const toLogical = (clientX: number, clientY: number) => {
    const rect = canvasRef.current!.getBoundingClientRect();
    return [((clientX - rect.left) / rect.width) * OFFICE_SIZE.width, ((clientY - rect.top) / rect.height) * OFFICE_SIZE.height] as const;
  };

  return (
    <div
      ref={wrapRef}
      className={full ? "relative flex h-full w-full items-center justify-center bg-black" : "relative w-full overflow-hidden rounded-xl border border-line bg-black"}
    >
      <canvas
        ref={canvasRef}
        className="block cursor-default touch-manipulation"
        onMouseMove={(e) => {
          const [x, y] = toLogical(e.clientX, e.clientY);
          const hit = engine.hitTest(x, y);
          engine.hovered = hit;
          e.currentTarget.style.cursor = hit ? "pointer" : "default";
        }}
        onMouseLeave={() => (engine.hovered = undefined)}
        onClick={(e) => {
          const [x, y] = toLogical(e.clientX, e.clientY);
          onSelect(engine.hitTest(x, y));
        }}
      />
      <button
        type="button"
        title={full ? "Sair da tela cheia" : "Tela cheia"}
        onClick={() => (document.fullscreenElement ? document.exitFullscreen() : wrapRef.current?.requestFullscreen?.())}
        className="absolute right-2 bottom-2 rounded-md bg-black/50 p-1.5 text-white/80 hover:bg-black/70"
      >
        {full ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
      </button>
    </div>
  );
}
