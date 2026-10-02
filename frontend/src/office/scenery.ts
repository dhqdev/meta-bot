// Cenário do escritório: piso, paredes, decoração e móveis (pré-desenhados) + partes animadas.
import { DOOR, GLASS, HEIGHT, PARTITION, PROPS, T, WIDTH, type Prop } from "./map";

export interface OfficeInfo {
  running: boolean;
  mode: string;
  source: string;
  mt5: { configured?: boolean; connected?: boolean };
  headline?: string;
  newsMood?: number;
  plan: Array<{ symbol: string; timeframe: string; strategy_name: string; direction: string }>;
  equity: Array<{ t: number; equity: number }>;
  balance?: number;
  currency?: string;
  killSwitch: boolean;
  dayBlocked: boolean;
  sessions: string[];
  hour: number; // hora local (São Paulo) com fração
  lastTradeAt: number;
  lastTradePnl?: number;
}

type Ctx = CanvasRenderingContext2D;

const C = {
  wood: ["#b8845a", "#ad7b52", "#c28e63", "#b27f55"],
  woodLine: "#8c6240",
  carpet: "#2f3e56",
  carpetDot: "#3a4b66",
  tech: "#2a3035",
  techLine: "#39424a",
  tileA: "#e6ebee",
  tileB: "#cdd5da",
  wallFace: "#e8dcc8",
  wallFace2: "#d9e4ef",
  wallTop: "#7a5c48",
  wallBase: "#6b4f3d",
  wallSide: "#4a3b30",
  deskTop: "#d9b38c",
  deskFront: "#a87c55",
  deskEdge: "#8a6444",
  metal: "#7f8c8d",
  dark: "#2d3436",
};

function rect(ctx: Ctx, x: number, y: number, w: number, h: number, c: string) {
  ctx.fillStyle = c;
  ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
}

function mkCanvas(w: number, h: number): [HTMLCanvasElement, Ctx] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  return [c, ctx];
}

export function pixelText(ctx: Ctx, text: string, x: number, y: number, color: string, size = 5, align: CanvasTextAlign = "left") {
  ctx.font = `${size}px "Press Start 2P", monospace`;
  ctx.textAlign = align;
  ctx.textBaseline = "top";
  ctx.fillStyle = color;
  ctx.fillText(text, x, y);
}

// ------------------------------------------------------------------ fundo
export function renderBackground(): HTMLCanvasElement {
  const [cv, ctx] = mkCanvas(WIDTH, HEIGHT);
  // piso de madeira (área aberta e corredores)
  for (let y = 2; y < 23; y++) {
    for (let x = 1; x < 39; x++) {
      const shade = C.wood[(x * 7 + y * 3 + ((x >> 1) % 2)) % C.wood.length];
      rect(ctx, x * T, y * T, T, T, shade);
      rect(ctx, x * T, y * T + 7, T, 1, C.woodLine);
      rect(ctx, x * T + ((y % 2) * 8 + 4), y * T, 1, 7, C.woodLine);
      rect(ctx, x * T + (((y + 1) % 2) * 8 + 4), y * T + 8, 1, 8, C.woodLine);
    }
  }
  // carpete da sala do gerente
  for (let y = 2; y <= 10; y++)
    for (let x = 28; x <= 38; x++) {
      rect(ctx, x * T, y * T, T, T, C.carpet);
      if ((x + y) % 2 === 0) rect(ctx, x * T + 7, y * T + 7, 2, 2, C.carpetDot);
    }
  // tapete da reunião
  rect(ctx, 29 * T + 4, 7 * T + 6, 7 * T - 8, 4 * T - 4, "#7b2d26");
  rect(ctx, 29 * T + 7, 7 * T + 9, 7 * T - 14, 4 * T - 10, "#96362d");
  // piso técnico da sala de servidores
  for (let y = 15; y <= 22; y++)
    for (let x = 1; x <= 10; x++) {
      rect(ctx, x * T, y * T, T, T, C.tech);
      rect(ctx, x * T, y * T, T, 1, C.techLine);
      rect(ctx, x * T, y * T, 1, T, C.techLine);
      if ((x * 3 + y) % 5 === 0) rect(ctx, x * T + 6, y * T + 6, 4, 4, "#323a40");
    }
  // copa (xadrez) e lounge
  for (let y = 12; y <= 15; y++)
    for (let x = 28; x <= 38; x++) rect(ctx, x * T, y * T, T, T, (x + y) % 2 ? C.tileA : C.tileB);
  rect(ctx, 28 * T + 6, 16 * T + 8, 10 * T, 6 * T - 6, "#16a085");
  rect(ctx, 28 * T + 10, 16 * T + 12, 10 * T - 8, 6 * T - 14, "#1abc9c");
  for (let i = 0; i < 9; i++) rect(ctx, 29 * T + i * 16, 17 * T + 4, 6, 2, "#48c9b0");
  // tapete da biblioteca
  rect(ctx, 19 * T + 4, 16 * T + 4, 6 * T, 4 * T, "#8e44ad");
  rect(ctx, 19 * T + 8, 16 * T + 8, 6 * T - 8, 4 * T - 8, "#9b59b6");
  // capacho da porta
  rect(ctx, DOOR.x * T, 22 * T + 4, DOOR.w * T, 10, "#5d4037");
  pixelText(ctx, "BEM-VINDO", DOOR.x * T + DOOR.w * 8, 22 * T + 6, "#d7ccc8", 4, "center");

  // parede do fundo (vista de frente)
  rect(ctx, 0, 0, WIDTH, 2 * T, C.wallFace);
  rect(ctx, 27 * T, 0, 12 * T, 2 * T, C.wallFace2);
  rect(ctx, 0, 0, WIDTH, 3, C.wallTop);
  rect(ctx, 0, 2 * T - 3, WIDTH, 3, C.wallBase);
  // paredes laterais e de baixo
  rect(ctx, 0, 0, T, HEIGHT, C.wallSide);
  rect(ctx, 39 * T, 0, T, HEIGHT, C.wallSide);
  rect(ctx, 0, 23 * T, WIDTH, T, C.wallSide);
  rect(ctx, T - 2, 2 * T, 2, 21 * T, "#5d4a3c");
  rect(ctx, 39 * T, 2 * T, 2, 21 * T, "#5d4a3c");
  // porta
  rect(ctx, DOOR.x * T - 2, 23 * T, DOOR.w * T + 4, T, "#3e2723");
  rect(ctx, DOOR.x * T, 23 * T + 2, DOOR.w * T, T - 2, "#8d6e63");
  rect(ctx, DOOR.x * T + DOOR.w * T - 6, 23 * T + 7, 2, 2, "#f1c40f");

  // molduras da parede (conteúdo é animado)
  frame(ctx, 36, 4, 70, 24, "#1b1b1b"); // TV
  frame(ctx, 8 * T + 2, 4, 44, 23, "#f5f5f5"); // janela 1
  frame(ctx, 11 * T + 2, 3, 92, 26, "#bdc3c7"); // quadro branco
  frame(ctx, 19 * T + 2, 4, 44, 23, "#f5f5f5"); // janela 2
  frame(ctx, 22 * T + 2, 3, 76, 26, "#111"); // telão
  frame(ctx, 29 * T, 4, 44, 23, "#f5f5f5"); // janela da sala do gerente
  // quadro na sala do gerente
  rect(ctx, 35 * T + 4, 6, 40, 20, "#6d4c41");
  rect(ctx, 35 * T + 7, 9, 34, 14, "#f39c12");
  rect(ctx, 35 * T + 10, 16, 28, 7, "#27ae60");
  rect(ctx, 35 * T + 26, 11, 8, 5, "#f9e79f");
  // pôster
  rect(ctx, 25 * T + 14, 6, 14, 20, "#2c3e50");
  pixelText(ctx, "R", 25 * T + 21, 9, "#e74c3c", 5, "center");
  pixelText(ctx, "1°", 25 * T + 21, 17, "#ecf0f1", 4, "center");
  // placa da sala de servidores
  rect(ctx, 2 * T, 14 * T - 10, 44, 8, "#1e272e");
  pixelText(ctx, "SERVIDOR MT5", 2 * T + 22, 14 * T - 9, "#2ecc71", 4, "center");

  // vidro vertical da sala do gerente (fica no fundo)
  for (const [x, y] of GLASS) {
    if (y === 11) continue;
    rect(ctx, x * T + 6, y * T - 6, 4, T + 6, "rgba(174, 214, 241, 0.55)");
    rect(ctx, x * T + 6, y * T - 6, 1, T + 6, "#85929e");
  }
  return cv;
}

function frame(ctx: Ctx, x: number, y: number, w: number, h: number, color: string) {
  rect(ctx, x - 2, y - 2, w + 4, h + 4, "#5d4a3c");
  rect(ctx, x, y, w, h, color);
}

// ------------------------------------------------------- parede animada
function skyColors(hour: number): [string, string, boolean] {
  if (hour < 5 || hour >= 20) return ["#0b1026", "#1c2541", true];
  if (hour < 7) return ["#f39c12", "#8e44ad", false];
  if (hour < 17) return ["#5dade2", "#aed6f1", false];
  if (hour < 19) return ["#e67e22", "#f5b041", false];
  return ["#2c3e50", "#5b2c6f", true];
}

function drawWindow(ctx: Ctx, x: number, y: number, w: number, h: number, info: OfficeInfo, t: number, seed: number) {
  const [top, bottom, night] = skyColors(info.hour);
  const g = ctx.createLinearGradient(0, y, 0, y + h);
  g.addColorStop(0, top);
  g.addColorStop(1, bottom);
  ctx.fillStyle = g;
  ctx.fillRect(x, y, w, h);
  if (night) {
    for (let i = 0; i < 7; i++) {
      const sx = x + ((seed * 13 + i * 17) % w);
      const sy = y + ((seed * 7 + i * 11) % (h - 10));
      if ((Math.floor(t * 2) + i) % 5) rect(ctx, sx, sy, 1, 1, "#fdfefe");
    }
    rect(ctx, x + w - 12, y + 3, 5, 5, "#f7f9f9");
    rect(ctx, x + w - 11, y + 3, 4, 4, top);
  } else {
    rect(ctx, x + 5, y + 4, 6, 6, "#f9e79f");
    const cx = x + ((t * 3 + seed * 20) % (w + 20)) - 10;
    rect(ctx, cx, y + 6, 12, 3, "#ffffff");
    rect(ctx, cx + 3, y + 4, 6, 2, "#ffffff");
  }
  // prédios
  for (let i = 0; i < w; i += 7) {
    const bh = 6 + ((i * 7 + seed * 3) % 9);
    rect(ctx, x + i, y + h - bh, 6, bh, night ? "#17202a" : "#566573");
    if (night)
      for (let wy = y + h - bh + 2; wy < y + h - 1; wy += 3) if ((i + wy + seed) % 3 === 0) rect(ctx, x + i + 2, wy, 1, 1, "#f7dc6f");
  }
  rect(ctx, x + w / 2 - 1, y, 2, h, "#f5f5f5");
  rect(ctx, x, y + h / 2 - 1, w, 2, "#f5f5f5");
}

export function renderWall(ctx: Ctx, info: OfficeInfo, t: number) {
  drawWindow(ctx, 8 * T + 2, 4, 44, 23, info, t, 1);
  drawWindow(ctx, 19 * T + 2, 4, 44, 23, info, t, 2);
  drawWindow(ctx, 29 * T, 4, 44, 23, info, t, 3);

  // TV de notícias da Nina
  const tvX = 36;
  rect(ctx, tvX, 4, 70, 24, "#101820");
  const mood = info.newsMood ?? 0;
  rect(ctx, tvX, 4, 70, 3, mood > 0.15 ? "#27ae60" : mood < -0.15 ? "#c0392b" : "#7f8c8d");
  pixelText(ctx, "NEWS", tvX + 2, 9, "#e74c3c", 4);
  if (Math.floor(t * 2) % 2) rect(ctx, tvX + 20, 9, 3, 3, "#e74c3c");
  const headline = (info.headline || "Aguardando as manchetes do dia...").toUpperCase();
  ctx.save();
  ctx.beginPath();
  ctx.rect(tvX + 2, 17, 66, 9);
  ctx.clip();
  const width = headline.length * 4;
  const off = (t * 18) % (width + 70);
  pixelText(ctx, headline, tvX + 68 - off, 19, "#f7dc6f", 4);
  ctx.restore();

  // quadro branco com o plano do Gerente
  const wbX = 11 * T + 2;
  rect(ctx, wbX, 3, 92, 26, "#fbfcfc");
  pixelText(ctx, "PLANO DO DIA", wbX + 3, 5, "#2c3e50", 4);
  const colors = ["#c0392b", "#2980b9", "#27ae60"];
  if (!info.plan.length) pixelText(ctx, info.running ? "sem setups agora" : "escritório fechado", wbX + 3, 14, "#7f8c8d", 4);
  info.plan.slice(0, 3).forEach((p, i) => {
    const dir = p.direction === "long" ? "↑" : p.direction === "short" ? "↓" : "↕";
    pixelText(ctx, `${dir} ${p.symbol} ${p.timeframe}`, wbX + 3, 12 + i * 6, colors[i], 4);
  });
  rect(ctx, wbX + 80, 24, 8, 2, "#e74c3c");

  // telão com a curva do patrimônio
  const scX = 22 * T + 2;
  rect(ctx, scX, 3, 76, 26, "#0b0f14");
  const eq = info.equity;
  if (eq.length > 1) {
    const vals = eq.map((p) => p.equity);
    const lo = Math.min(...vals);
    const hi = Math.max(...vals);
    const span = hi - lo || 1;
    const up = vals[vals.length - 1] >= vals[0];
    ctx.fillStyle = up ? "#2ecc71" : "#e74c3c";
    for (let i = 0; i < 70; i++) {
      const v = vals[Math.floor((i / 70) * (vals.length - 1))];
      const yy = 25 - ((v - lo) / span) * 12;
      ctx.fillRect(scX + 3 + i, Math.round(yy), 1, 1);
    }
  } else {
    for (let i = 0; i < 70; i++) rect(ctx, scX + 3 + i, 20 + Math.round(Math.sin(i / 6 + t) * 2), 1, 1, "#34495e");
  }
  const bal = info.balance != null ? info.balance.toLocaleString("pt-BR", { maximumFractionDigits: 0 }) : "--";
  pixelText(ctx, `${info.mode === "live" ? "REAL" : "SIM"} ${bal}`, scX + 3, 5, info.mode === "live" ? "#f5b041" : "#5dade2", 4);

  // relógio (horário de Brasília)
  const cx = 34 * T;
  const cy = 16;
  ctx.fillStyle = "#fdfefe";
  ctx.beginPath();
  ctx.arc(cx, cy, 9, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = "#5d4a3c";
  ctx.lineWidth = 2;
  ctx.stroke();
  const h = info.hour % 12;
  const m = (info.hour % 1) * 60;
  hand(ctx, cx, cy, (h / 12) * Math.PI * 2, 5, "#2c3e50");
  hand(ctx, cx, cy, (m / 60) * Math.PI * 2, 7, "#2c3e50");
  // sessões abertas embaixo do relógio
  const short: Record<string, string> = { Sydney: "SYD", "Tóquio": "TKY", Londres: "LDN", "Nova York": "NY", B3: "B3" };
  const label = info.sessions.length ? info.sessions.map((s) => short[s] ?? s.slice(0, 3)).join(" ") : "FECHADO";
  pixelText(ctx, label, cx, 27, "#34495e", 3, "center");
}

function hand(ctx: Ctx, cx: number, cy: number, angle: number, len: number, color: string) {
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx, cy);
  ctx.lineTo(cx + Math.sin(angle) * len, cy - Math.cos(angle) * len);
  ctx.stroke();
}

// --------------------------------------------------------------- móveis
export interface Drawable {
  sortY: number;
  draw: (ctx: Ctx, t: number, info: OfficeInfo) => void;
}

const imgCache = new Map<string, HTMLCanvasElement>();

function cached(key: string, w: number, h: number, paint: (ctx: Ctx) => void): HTMLCanvasElement {
  let img = imgCache.get(key);
  if (!img) {
    const [cv, c] = mkCanvas(w, h);
    paint(c);
    img = cv;
    imgCache.set(key, img);
  }
  return img;
}

function deskImage(): HTMLCanvasElement {
  return cached("desk", 48, 24, (c) => {
    rect(c, 0, 4, 48, 12, C.deskTop);
    rect(c, 0, 4, 48, 1, "#e8c9a6");
    rect(c, 0, 16, 48, 7, C.deskFront);
    rect(c, 0, 22, 48, 2, C.deskEdge);
    rect(c, 2, 16, 3, 8, C.deskEdge);
    rect(c, 43, 16, 3, 8, C.deskEdge);
    rect(c, 30, 18, 12, 3, "#8a6444");
    rect(c, 35, 19, 2, 1, "#f1c40f");
  });
}

function monitor(ctx: Ctx, x: number, y: number, w = 16, h = 11) {
  rect(ctx, x - 1, y - 1, w + 2, h + 2, "#1b1b1b");
  rect(ctx, x + w / 2 - 2, y + h + 1, 4, 3, "#2d3436");
  rect(ctx, x + w / 2 - 4, y + h + 3, 8, 1, "#2d3436");
}

function screenContent(ctx: Ctx, owner: string, x: number, y: number, w: number, h: number, t: number, info: OfficeInfo) {
  const on = info.running || owner === "infra" || owner === "cashier";
  rect(ctx, x, y, w, h, on ? "#0e1a24" : "#1e272e");
  if (!on) return;
  const f = Math.floor(t * 4);
  switch (owner) {
    case "news":
      for (let i = 0; i < 4; i++) rect(ctx, x + 1, y + 1 + i * 2.5, ((i * 5 + f) % 6) + 7, 1, i === 0 ? "#e74c3c" : "#ecf0f1");
      break;
    case "schedule":
      for (let i = 0; i < 3; i++) for (let j = 0; j < 4; j++) rect(ctx, x + 1 + j * 3.5, y + 1 + i * 3, 3, 2, (i * 4 + j) === f % 12 ? "#f39c12" : "#566573");
      break;
    case "strategist":
    case "strategist2": {
      let v = h / 2;
      for (let i = 0; i < w - 2; i += 2) {
        const up = Math.sin(i * 0.9 + (owner === "strategist2" ? 3 : 0) + Math.floor(t)) > 0;
        const len = 2 + ((i * 7) % 3);
        v = Math.max(1, Math.min(h - 4, v + (up ? -1 : 1)));
        rect(ctx, x + 1 + i, y + v, 1, len, up ? "#2ecc71" : "#e74c3c");
      }
      break;
    }
    case "auditor":
      for (let i = 0; i < 4; i++) {
        rect(ctx, x + 1, y + 1 + i * 2.5, 9, 1, "#bdc3c7");
        if (i <= f % 5) rect(ctx, x + 12, y + 1 + i * 2.5, 2, 1, "#2ecc71");
      }
      break;
    case "risk": {
      const danger = info.killSwitch || info.dayBlocked;
      ctx.strokeStyle = danger ? "#e74c3c" : "#2ecc71";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(x + w / 2, y + h - 2, 5, Math.PI, 0);
      ctx.stroke();
      const a = Math.PI + (danger ? 0.9 : 0.25 + 0.1 * Math.sin(t)) * Math.PI;
      hand(ctx, x + w / 2, y + h - 2, a + Math.PI / 2, 5, "#f1c40f");
      break;
    }
    case "cashier":
      for (let i = 0; i < 4; i++) rect(ctx, x + 1, y + 1 + i * 2.5, 10, 1, (i + f) % 2 ? "#2ecc71" : "#e74c3c");
      break;
    case "infra":
      for (let i = 0; i < 4; i++) rect(ctx, x + 1, y + 1 + i * 2.5, ((i * 3 + f) % 8) + 4, 1, "#2ecc71");
      if (f % 2) rect(ctx, x + 1, y + h - 2, 2, 1, "#2ecc71");
      break;
  }
}

function chairImage(face: string, part: "seat" | "back"): HTMLCanvasElement {
  return cached(`chair-${face}-${part}`, 16, 20, (c) => {
    if (part === "seat") {
      rect(c, 3, 9, 10, 5, "#34495e");
      rect(c, 7, 14, 2, 3, "#2d3436");
      rect(c, 4, 17, 8, 1, "#2d3436");
    } else if (face === "up") {
      rect(c, 3, 12, 10, 6, "#2c3e50");
      rect(c, 4, 13, 8, 3, "#34495e");
    } else {
      rect(c, 3, 1, 10, 9, "#2c3e50");
      rect(c, 4, 2, 8, 6, "#34495e");
    }
  });
}

function drawPlant(ctx: Ctx, x: number, y: number, big: boolean, t: number) {
  const sway = Math.round(Math.sin(t * 1.3 + x) * 0.6);
  if (big) {
    rect(ctx, x + 4, y + 8, 8, 8, "#a0522d");
    rect(ctx, x + 3, y + 8, 10, 2, "#8b4513");
    rect(ctx, x + 2 + sway, y - 8, 12, 12, "#1e8449");
    rect(ctx, x + 4 + sway, y - 12, 8, 6, "#27ae60");
    rect(ctx, x + sway, y - 3, 5, 5, "#229954");
    rect(ctx, x + 11 + sway, y - 4, 5, 5, "#229954");
  } else {
    rect(ctx, x + 5, y + 9, 6, 6, "#d35400");
    rect(ctx, x + 3 + sway, y + 2, 10, 8, "#27ae60");
    rect(ctx, x + 5 + sway, y, 6, 3, "#2ecc71");
  }
}

function drawProp(p: Prop): Drawable[] {
  const px = p.x * T;
  const py = p.y * T;
  const bottom = (p.y + p.h) * T;
  switch (p.kind) {
    case "desk":
      return [
        {
          sortY: bottom,
          draw: (ctx, t, info) => {
            ctx.drawImage(deskImage(), px, py - 8);
            // objetos da mesa
            rect(ctx, px + 3, py - 1, 6, 4, "#ecf0f1");
            if (p.owner === "strategist") {
              monitor(ctx, px + 8, py - 12);
              monitor(ctx, px + 25, py - 12);
              screenContent(ctx, "strategist", px + 8, py - 12, 16, 11, t, info);
              screenContent(ctx, "strategist2", px + 25, py - 12, 16, 11, t, info);
            } else {
              monitor(ctx, px + 16, py - 12);
              screenContent(ctx, p.owner || "", px + 16, py - 12, 16, 11, t, info);
              rect(ctx, px + 38, py - 2, 4, 5, "#ecf0f1");
              rect(ctx, px + 42, py, 1, 2, "#ecf0f1");
            }
            rect(ctx, px + 14, py + 4, 20, 3, "#2d3436");
            // plaquinha com o cargo
            const labels: Record<string, string> = { news: "NOTÍCIAS", schedule: "HORÁRIOS", strategist: "ESTRATÉGIA", auditor: "AUDITORIA", risk: "RISCO", cashier: "CAIXA", infra: "TI" };
            rect(ctx, px + 2, py + 9, 26, 5, "#2c3e50");
            pixelText(ctx, labels[p.owner || ""] || "", px + 15, py + 10, "#f7dc6f", 3, "center");
          },
        },
      ];
    case "chair":
      return [
        { sortY: py + 2, draw: (ctx) => ctx.drawImage(chairImage(p.face || "up", "seat"), px, py - 2) },
        { sortY: p.face === "up" ? py + 15 : py + 1, draw: (ctx) => ctx.drawImage(chairImage(p.face || "up", "back"), px, p.face === "up" ? py - 2 : py - 10) },
      ];
    case "managerDesk":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px, py - 6, 64, 14, "#5d4037");
            rect(ctx, px, py - 6, 64, 1, "#795548");
            rect(ctx, px, py + 8, 64, 8, "#4e342e");
            rect(ctx, px + 20, py - 9, 18, 10, "#bdc3c7");
            rect(ctx, px + 21, py - 8, 16, 1, "#95a5a6");
            rect(ctx, px + 27, py - 5, 4, 3, "#ecf0f1");
            rect(ctx, px + 4, py - 4, 8, 6, "#ecf0f1");
            rect(ctx, px + 48, py - 5, 5, 6, "#c0392b");
            rect(ctx, px + 49, py - 7, 3, 2, "#ecf0f1");
            pixelText(ctx, "GERENTE", px + 32, py + 10, "#f7dc6f", 3, "center");
          },
        },
      ];
    case "meetingTable":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            rect(ctx, px + 2, py - 4, 76, 30, "#6d4c41");
            rect(ctx, px + 4, py - 2, 72, 24, "#8d6e63");
            rect(ctx, px + 2, py + 26, 76, 4, "#4e342e");
            rect(ctx, px + 10, py + 4, 8, 6, "#ecf0f1");
            rect(ctx, px + 60, py + 10, 8, 6, "#ecf0f1");
            rect(ctx, px + 36, py + 6, 8, 8, "#2c3e50");
            if (Math.floor(t) % 2) rect(ctx, px + 39, py + 9, 2, 2, "#2ecc71");
          },
        },
      ];
    case "plant":
    case "bigPlant":
      return [{ sortY: bottom, draw: (ctx, t) => drawPlant(ctx, px, py, p.kind === "bigPlant", t) }];
    case "rack":
      return [
        {
          sortY: bottom,
          draw: (ctx, t, info) => {
            rect(ctx, px + 1, py - 10, 14, 42, "#17202a");
            rect(ctx, px + 2, py - 9, 12, 40, "#212f3d");
            const ok = info.mt5.connected;
            const sim = !info.mt5.configured || !ok;
            for (let i = 0; i < 7; i++) {
              rect(ctx, px + 3, py - 7 + i * 5, 10, 3, "#2c3e50");
              const blink = (Math.floor(t * (3 + i)) + i + p.x) % 3 !== 0;
              const color = ok ? "#2ecc71" : sim && info.mt5.configured ? "#e74c3c" : "#f39c12";
              if (blink) rect(ctx, px + 4 + ((i + p.x) % 3) * 3, py - 6 + i * 5, 1, 1, color);
            }
          },
        },
      ];
    case "cooling":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            rect(ctx, px, py - 6, 32, 22, "#aeb6bf");
            rect(ctx, px + 2, py - 4, 28, 14, "#85929e");
            const a = t * 10;
            for (let k = 0; k < 2; k++) {
              const cx = px + 9 + k * 14;
              const cy = py + 3;
              rect(ctx, cx - 5, cy - 5, 10, 10, "#566573");
              rect(ctx, cx + Math.round(Math.cos(a) * 3) - 1, cy + Math.round(Math.sin(a) * 3) - 1, 2, 2, "#d5dbdb");
            }
          },
        },
      ];
    case "safe":
      return [
        {
          sortY: bottom,
          draw: (ctx, t, info) => {
            rect(ctx, px, py - 10, 32, 42, "#2c3e50");
            rect(ctx, px + 2, py - 8, 28, 38, "#34495e");
            rect(ctx, px + 4, py - 6, 24, 7, "#0b0f14");
            const bal = info.balance != null ? Math.round(info.balance).toString().slice(-6) : "------";
            pixelText(ctx, (info.currency === "BRL" ? "R$" : "$") + bal, px + 16, py - 5, "#2ecc71", 4, "center");
            const spin = Date.now() / 1000 - info.lastTradeAt < 3 ? t * 8 : 0;
            ctx.strokeStyle = "#bdc3c7";
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.arc(px + 16, py + 14, 6, 0, Math.PI * 2);
            ctx.stroke();
            hand(ctx, px + 16, py + 14, spin, 5, "#f1c40f");
            rect(ctx, px + 26, py + 8, 2, 12, "#95a5a6");
            pixelText(ctx, "COFRE", px + 16, py + 24, "#f7dc6f", 3, "center");
          },
        },
      ];
    case "counter":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px, py - 4, 32, 12, "#a87c55");
            rect(ctx, px, py + 8, 32, 8, "#8a6444");
            for (let i = 0; i < 3; i++) {
              rect(ctx, px + 3 + i * 9, py - 3 - i, 7, 4 + i, "#27ae60");
              rect(ctx, px + 3 + i * 9, py - 3 - i, 7, 1, "#58d68d");
            }
          },
        },
      ];
    case "bookshelf":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px, py - 18, 32, 34, "#6e2c00");
            const colors = ["#c0392b", "#2980b9", "#27ae60", "#f39c12", "#8e44ad", "#16a085", "#d35400"];
            for (let row = 0; row < 3; row++) {
              rect(ctx, px + 2, py - 16 + row * 10, 28, 8, "#4a2511");
              for (let b = 0; b < 7; b++) rect(ctx, px + 3 + b * 4, py - 15 + row * 10 + (b % 2), 3, 7 - (b % 2), colors[(b + row * 2 + p.x) % colors.length]);
            }
            pixelText(ctx, "SKILLS", px + 16, py + 12, "#f7dc6f", 3, "center");
          },
        },
      ];
    case "kitchen":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            rect(ctx, px, py - 4, p.w * T, 10, "#bdc3c7");
            rect(ctx, px, py + 6, p.w * T, 10, "#7f8c8d");
            for (let i = 0; i < p.w; i++) rect(ctx, px + i * T + 7, py + 9, 2, 3, "#566573");
            // máquina de café
            const mx = px + 20;
            rect(ctx, mx, py - 16, 14, 16, "#2c3e50");
            rect(ctx, mx + 2, py - 14, 10, 5, "#566573");
            rect(ctx, mx + 5, py - 6, 4, 4, "#ecf0f1");
            if (Math.floor(t * 2) % 2) rect(ctx, mx + 3, py - 13, 2, 2, "#e74c3c");
            for (let k = 0; k < 3; k++) {
              const sy = py - 20 - ((t * 10 + k * 5) % 12);
              rect(ctx, mx + 6 + Math.round(Math.sin(t * 3 + k) * 2), sy, 1, 2, "rgba(255,255,255,0.6)");
            }
            // micro-ondas e xícaras
            rect(ctx, px + 44, py - 12, 18, 11, "#ecf0f1");
            rect(ctx, px + 46, py - 10, 11, 7, "#2c3e50");
            rect(ctx, px + 4, py - 6, 4, 4, "#ecf0f1");
            rect(ctx, px + 10, py - 6, 4, 4, "#e67e22");
          },
        },
      ];
    case "fridge":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px + 1, py - 16, 14, 32, "#ecf0f1");
            rect(ctx, px + 1, py - 4, 14, 1, "#bdc3c7");
            rect(ctx, px + 12, py - 12, 1, 5, "#95a5a6");
            rect(ctx, px + 12, py, 1, 6, "#95a5a6");
            rect(ctx, px + 3, py - 14, 3, 3, "#f1c40f");
          },
        },
      ];
    case "cooler":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px + 4, py - 12, 8, 9, "rgba(133, 193, 233, 0.9)");
            rect(ctx, px + 3, py - 3, 10, 18, "#ecf0f1");
            rect(ctx, px + 6, py + 1, 2, 2, "#3498db");
            rect(ctx, px + 9, py + 1, 2, 2, "#e74c3c");
          },
        },
      ];
    case "sofa":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px - 2, py - 8, p.w * T + 4, 12, "#922b21");
            rect(ctx, px, py + 2, p.w * T, 12, "#c0392b");
            rect(ctx, px - 4, py - 2, 5, 16, "#922b21");
            rect(ctx, px + p.w * T - 1, py - 2, 5, 16, "#922b21");
            for (let i = 1; i < p.w; i++) rect(ctx, px + i * T, py + 3, 1, 10, "#a93226");
          },
        },
      ];
    case "coffeeTable":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px + 4, py + 2, p.w * T - 8, 9, "#6d4c41");
            rect(ctx, px + 4, py + 11, p.w * T - 8, 3, "#4e342e");
            rect(ctx, px + 12, py + 3, 5, 4, "#ecf0f1");
            rect(ctx, px + 24, py + 3, 8, 5, "#2980b9");
          },
        },
      ];
    case "printer":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            rect(ctx, px + 2, py - 6, 26, 8, "#95a5a6");
            rect(ctx, px + 2, py + 2, 26, 13, "#bdc3c7");
            rect(ctx, px + 6, py - 4, 18, 2, "#2c3e50");
            const out = (t * 4) % 12;
            if (out < 6) rect(ctx, px + 9, py - 4 - out, 12, out, "#fdfefe");
            rect(ctx, px + 22, py + 5, 2, 2, "#2ecc71");
          },
        },
      ];
    case "arcade":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            rect(ctx, px + 1, py - 18, 14, 34, "#4a235a");
            rect(ctx, px + 3, py - 15, 10, 9, "#000");
            const hue = Math.floor(t * 3) % 3;
            rect(ctx, px + 4 + ((t * 8) % 7), py - 13, 2, 2, ["#f1c40f", "#e74c3c", "#2ecc71"][hue]);
            rect(ctx, px + 3, py - 4, 10, 4, "#6c3483");
            rect(ctx, px + 5, py - 3, 2, 2, "#e74c3c");
            rect(ctx, px + 9, py - 3, 2, 2, "#f1c40f");
          },
        },
      ];
    case "armchair":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px, py - 6, 16, 10, "#1f618d");
            rect(ctx, px + 1, py + 3, 14, 11, "#2980b9");
            rect(ctx, px - 1, py, 3, 12, "#1f618d");
            rect(ctx, px + 14, py, 3, 12, "#1f618d");
          },
        },
      ];
    case "trash":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px + 5, py + 6, 6, 8, "#7f8c8d");
            rect(ctx, px + 4, py + 5, 8, 2, "#95a5a6");
          },
        },
      ];
    case "lamp":
      return [
        {
          sortY: bottom,
          draw: (ctx, _t, info) => {
            rect(ctx, px + 7, py - 8, 2, 22, "#2c3e50");
            rect(ctx, px + 3, py - 14, 10, 7, info.running ? "#f9e79f" : "#b7950b");
            rect(ctx, px + 5, py + 13, 6, 2, "#2c3e50");
          },
        },
      ];
    case "standTable":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            rect(ctx, px + 2, py - 2, p.w * T - 4, 6, "#d9b38c");
            rect(ctx, px + p.w * 8 - 1, py + 4, 2, 10, "#7f8c8d");
            rect(ctx, px + 6, py - 4, 6, 3, "#e74c3c");
            rect(ctx, px + 16, py - 3, 4, 2, "#ecf0f1");
          },
        },
      ];
    default:
      return [];
  }
}

export function buildDrawables(): Drawable[] {
  const list: Drawable[] = PROPS.flatMap(drawProp);
  // vidro horizontal da sala do gerente e divisória dos servidores (ordenados: cobrem quem está atrás)
  for (const [x, y] of GLASS) {
    if (y !== 11) continue;
    list.push({
      sortY: y * T + 14,
      draw: (ctx) => {
        rect(ctx, x * T, y * T - 6, T, 18, "rgba(174, 214, 241, 0.35)");
        rect(ctx, x * T, y * T - 6, T, 1, "#aeb6bf");
        rect(ctx, x * T, y * T + 11, T, 2, "#85929e");
      },
    });
  }
  for (const [x, y] of PARTITION) {
    list.push({
      sortY: y * T + 14,
      draw: (ctx) => {
        rect(ctx, x * T, y * T + 2, T, 12, "rgba(133, 146, 158, 0.45)");
        rect(ctx, x * T, y * T + 2, T, 1, "#aeb6bf");
        rect(ctx, x * T, y * T + 12, T, 2, "#566573");
      },
    });
  }
  return list;
}

// ------------------------------------------------------------ iluminação
export function renderLighting(ctx: Ctx, info: OfficeInfo, t: number) {
  const [, , night] = skyColors(info.hour);
  if (!info.running) {
    ctx.fillStyle = "rgba(10, 15, 40, 0.38)";
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
  } else if (night) {
    ctx.fillStyle = "rgba(20, 30, 70, 0.12)";
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
  }
  if (info.killSwitch) {
    const a = 0.12 + 0.1 * Math.sin(t * 6);
    ctx.fillStyle = `rgba(231, 76, 60, ${a})`;
    ctx.fillRect(0, 0, WIDTH, HEIGHT);
  }
}
