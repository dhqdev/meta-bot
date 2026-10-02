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
/** Detalhe do cenário: o fundo é desenhado em 2× para ter meio pixel (veios da madeira, rejunte, brilhos). */
export const DETAIL = 2;

/** Retângulo sem arredondar (aceita meio pixel quando o canvas está em 2× ou mais). */
function fine(ctx: Ctx, x: number, y: number, w: number, h: number, c: string) {
  ctx.fillStyle = c;
  ctx.fillRect(x, y, w, h);
}

/** Número pseudoaleatório estável por posição (o piso fica igual em todo quadro). */
function hash(x: number, y: number, k = 0) {
  let h = (x * 374761393 + y * 668265263 + k * 2147483647) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
}

function woodFloor(ctx: Ctx, x0: number, y0: number, x1: number, y1: number) {
  // tábuas de 4 px de altura, comprimentos variados, três tons quentes e veios finos
  const tones = ["#c08a5b", "#b98154", "#c79363", "#b47c50", "#bd875a"];
  for (let y = y0; y < y1; y += 4) {
    const row = y / 4;
    let x = x0 - Math.floor(hash(row, 7) * 28);
    while (x < x1) {
      const len = 22 + Math.floor(hash(x, row) * 26);
      const c = tones[Math.floor(hash(row, x, 3) * tones.length)];
      const sx = Math.max(x, x0);
      const ex = Math.min(x + len, x1);
      if (ex > sx) {
        fine(ctx, sx, y, ex - sx, 4, c);
        fine(ctx, sx, y, ex - sx, 0.5, mixHex(c, "#ffffff", 0.16)); // brilho na borda de cima
        fine(ctx, sx, y + 3.5, ex - sx, 0.5, "#7d5134"); // junta entre as tábuas
        for (let g = 0; g < (ex - sx) / 6; g++) {
          const gx = sx + hash(x, row, g) * (ex - sx - 3);
          fine(ctx, gx, y + 1 + Math.floor(hash(g, x, row) * 4) * 0.5, 2 + hash(g, row) * 3, 0.5, mixHex(c, "#5d3a22", 0.28));
        }
        if (hash(x, row, 9) > 0.86) fine(ctx, sx + (ex - sx) / 2, y + 1.5, 1, 1, mixHex(c, "#4a2c18", 0.45)); // nó da madeira
        if (x + len < x1 && x + len > x0) {
          fine(ctx, x + len - 0.5, y, 0.5, 3.5, "#7d5134");
          fine(ctx, x + len - 2, y + 1.5, 0.5, 0.5, "#5a3a26"); // prego
        }
      }
      x += len;
    }
  }
}

function mixHex(a: string, b: string, k: number): string {
  const p = (c: string) => {
    const n = parseInt(c.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  };
  const [r1, g1, b1] = p(a);
  const [r2, g2, b2] = p(b);
  const f = (u: number, v: number) => Math.round(u + (v - u) * k).toString(16).padStart(2, "0");
  return `#${f(r1, r2)}${f(g1, g2)}${f(b1, b2)}`;
}

function bevelTile(ctx: Ctx, x: number, y: number, c: string, size = T) {
  fine(ctx, x, y, size, size, c);
  fine(ctx, x, y, size, 0.5, mixHex(c, "#ffffff", 0.35));
  fine(ctx, x, y, 0.5, size, mixHex(c, "#ffffff", 0.25));
  fine(ctx, x, y + size - 0.5, size, 0.5, mixHex(c, "#000000", 0.28));
  fine(ctx, x + size - 0.5, y, 0.5, size, mixHex(c, "#000000", 0.2));
}

function rug(ctx: Ctx, x: number, y: number, w: number, h: number, base: string, border: string, accent: string) {
  fine(ctx, x + 1, y + 1, w, h, "rgba(0,0,0,0.22)"); // sombra
  fine(ctx, x, y, w, h, border);
  fine(ctx, x + 2, y + 2, w - 4, h - 4, base);
  fine(ctx, x + 3.5, y + 3.5, w - 7, 0.5, accent);
  fine(ctx, x + 3.5, y + h - 4, w - 7, 0.5, accent);
  fine(ctx, x + 3.5, y + 3.5, 0.5, h - 7, accent);
  fine(ctx, x + w - 4, y + 3.5, 0.5, h - 7, accent);
  // losangos no meio
  for (let yy = y + 7; yy < y + h - 8; yy += 6)
    for (let xx = x + 7 + ((yy - y) % 12 ? 3 : 0); xx < x + w - 8; xx += 6) {
      fine(ctx, xx + 1, yy, 1, 0.5, accent);
      fine(ctx, xx + 0.5, yy + 0.5, 2, 0.5, accent);
      fine(ctx, xx + 1, yy + 1, 1, 0.5, accent);
    }
  // franjas nas pontas
  for (let xx = x + 1; xx < x + w - 1; xx += 1.5) {
    fine(ctx, xx, y - 1.5, 0.5, 1.5, border);
    fine(ctx, xx, y + h, 0.5, 1.5, border);
  }
}

/** Sombras dos móveis no chão (dão profundidade, como nos jogos de fazenda). */
function furnitureShadows(ctx: Ctx) {
  for (const p of PROPS) {
    if (p.kind === "chair" || p.kind === "trash") continue;
    const x = p.x * T;
    const bottom = (p.y + p.h) * T;
    const w = p.w * T;
    ctx.fillStyle = "rgba(40, 22, 10, 0.22)";
    ctx.beginPath();
    ctx.ellipse(x + w / 2 + 1.5, bottom - 0.5, w / 2 + 1, 3, 0, 0, Math.PI * 2);
    ctx.fill();
  }
}

export function renderBackground(): HTMLCanvasElement {
  const [cv, ctx] = mkCanvas(WIDTH * DETAIL, HEIGHT * DETAIL);
  ctx.scale(DETAIL, DETAIL);
  // piso de madeira (área aberta e corredores)
  woodFloor(ctx, T, 2 * T, 39 * T, 23 * T);
  // carpete da sala do gerente (trama fina)
  for (let y = 2; y <= 10; y++)
    for (let x = 28; x <= 38; x++) {
      fine(ctx, x * T, y * T, T, T, C.carpet);
      for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) if ((i + j + x + y) % 2 === 0) fine(ctx, x * T + i * 4 + 1, y * T + j * 4 + 1, 1.5, 1.5, C.carpetDot);
    }
  // tapete da reunião
  rug(ctx, 29 * T + 4, 7 * T + 6, 7 * T - 8, 4 * T - 4, "#96362d", "#6e241e", "#d4a24c");
  // piso técnico da sala de servidores
  for (let y = 15; y <= 22; y++)
    for (let x = 1; x <= 10; x++) {
      bevelTile(ctx, x * T, y * T, C.tech);
      if ((x * 3 + y) % 5 === 0) for (let k = 0; k < 4; k++) fine(ctx, x * T + 4, y * T + 4 + k * 2, 8, 0.75, "#1c2125"); // grade de ventilação
      else fine(ctx, x * T + 2, y * T + 2, 0.75, 0.75, "#4a545c");
    }
  // copa (piso de cerâmica xadrez com rejunte)
  for (let y = 12; y <= 15; y++) for (let x = 28; x <= 38; x++) for (let h = 0; h < 2; h++) for (let v = 0; v < 2; v++) bevelTile(ctx, x * T + h * 8, y * T + v * 8, (x * 2 + h + y * 2 + v) % 2 ? C.tileA : C.tileB, 8);
  // lounge
  rug(ctx, 28 * T + 6, 16 * T + 8, 10 * T, 6 * T - 6, "#1abc9c", "#11806a", "#7ee0c9");
  // tapete da biblioteca
  rug(ctx, 19 * T + 4, 16 * T + 4, 6 * T, 4 * T, "#8e44ad", "#5b2c6f", "#d2a8e8");
  // capacho da porta
  fine(ctx, DOOR.x * T, 22 * T + 4, DOOR.w * T, 10, "#5d4037");
  for (let i = 0; i < DOOR.w * T; i += 2) fine(ctx, DOOR.x * T + i, 22 * T + 4, 1, 10, "#6d4c41");
  pixelText(ctx, "BEM-VINDO", DOOR.x * T + DOOR.w * 8, 22 * T + 6, "#efe5df", 4, "center");
  furnitureShadows(ctx);

  // parede do fundo (vista de frente)
  for (const [x0, x1, paper] of [
    [0, 27 * T, C.wallFace],
    [27 * T, WIDTH, C.wallFace2],
  ] as Array<[number, number, string]>) {
    fine(ctx, x0, 0, x1 - x0, 2 * T, paper);
    // papel de parede: listras finas e florzinhas
    for (let x = x0; x < x1; x += 6) fine(ctx, x, 3, 1, 19, mixHex(paper, "#8d6e63", 0.12));
    for (let x = x0 + 3; x < x1; x += 12) for (let y = 6; y < 20; y += 8) fine(ctx, x, y + ((x / 12) % 2) * 4, 1, 1, mixHex(paper, "#b03a2e", 0.25));
    // lambri de madeira embaixo
    fine(ctx, x0, 21, x1 - x0, 8, "#8d5f3e");
    fine(ctx, x0, 21, x1 - x0, 1, "#b07a52");
    for (let x = x0 + 2; x < x1 - 4; x += 16) {
      fine(ctx, x, 23, 12, 4.5, "#7a5134");
      fine(ctx, x, 23, 12, 0.5, "#5e3d26");
      fine(ctx, x, 27, 12, 0.5, "#a77650");
    }
  }
  rect(ctx, 0, 0, WIDTH, 3, C.wallTop);
  fine(ctx, 0, 3, WIDTH, 0.5, "#a07a60"); // moldura de cima
  rect(ctx, 0, 2 * T - 3, WIDTH, 3, C.wallBase);
  fine(ctx, 0, 2 * T - 3, WIDTH, 0.5, "#8a6a52");
  // sombra da parede no chão
  for (let i = 0; i < 4; i++) fine(ctx, T, 2 * T + i, WIDTH - 2 * T, 1, `rgba(40,20,10,${0.28 - i * 0.07})`);
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
    const [cv, c] = mkCanvas(w * DETAIL, h * DETAIL);
    c.scale(DETAIL, DETAIL);
    paint(c);
    img = cv;
    imgCache.set(key, img);
  }
  return img;
}

/** Desenha uma imagem pré-pronta (em 2×) no tamanho lógico. */
function blit(ctx: Ctx, img: HTMLCanvasElement, x: number, y: number) {
  ctx.drawImage(img, x, y, img.width / DETAIL, img.height / DETAIL);
}

const lighten = (c: string, k = 0.25) => mixHex(c, "#ffffff", k);
const darken = (c: string, k = 0.3) => mixHex(c, "#1a1020", k);

function deskImage(): HTMLCanvasElement {
  return cached("desk", 48, 24, (c) => {
    // tampo com veio da madeira, borda clara e quina escura
    fine(c, 0, 4, 48, 12, C.deskTop);
    for (let i = 0; i < 7; i++) fine(c, 2 + ((i * 13) % 40), 6 + (i % 4) * 2.5, 6 + (i % 3) * 3, 0.5, "#c99f76");
    fine(c, 0, 4, 48, 1, "#f0d3b2");
    fine(c, 0, 15, 48, 1, "#b88a60");
    // frente com gaveteiro
    fine(c, 0, 16, 48, 7, C.deskFront);
    fine(c, 0, 16, 48, 0.5, "#c4946a");
    fine(c, 0, 22, 48, 2, C.deskEdge);
    fine(c, 2, 16, 3, 8, C.deskEdge);
    fine(c, 43, 16, 3, 8, C.deskEdge);
    fine(c, 29, 17, 13, 5, "#99704b");
    fine(c, 29, 17, 13, 0.5, "#b78a62");
    fine(c, 29, 19.5, 13, 0.5, "#7d583a");
    fine(c, 34.5, 18, 3, 1, "#f1c40f");
    fine(c, 34.5, 20.5, 3, 1, "#f1c40f");
  });
}

function monitor(ctx: Ctx, x: number, y: number, w = 16, h = 11) {
  fine(ctx, x + w / 2 - 1.5, y + h + 1, 3, 3, "#3d4448");
  fine(ctx, x + w / 2 - 4, y + h + 3, 8, 1.5, "#2d3436");
  fine(ctx, x + w / 2 - 4, y + h + 3, 8, 0.5, "#596166");
  fine(ctx, x - 1.5, y - 1.5, w + 3, h + 3, "#16191c");
  fine(ctx, x - 1.5, y - 1.5, w + 3, 0.5, "#4a5257");
  fine(ctx, x + w - 1, y + h + 0.5, 1, 0.5, "#2ecc71"); // luz de ligado
}

function screenContent(ctx: Ctx, owner: string, x: number, y: number, w: number, h: number, t: number, info: OfficeInfo) {
  const on = info.running || owner === "infra" || owner === "cashier";
  fine(ctx, x, y, w, h, on ? "#0e1a24" : "#1e272e");
  fine(ctx, x + 0.5, y + 0.5, 3, 0.5, "rgba(255,255,255,0.18)");
  fine(ctx, x + 0.5, y + 0.5, 0.5, 2, "rgba(255,255,255,0.18)");
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
    const cloth = "#3b4f6b";
    if (part === "seat") {
      // assento estofado, coluna e rodinhas
      fine(c, 7, 13, 2, 4, "#2d3436");
      fine(c, 3, 16.5, 10, 1, "#2d3436");
      for (const x of [3, 7.5, 12]) fine(c, x - 0.5, 17, 1.5, 1.5, "#111");
      fine(c, 3, 9, 10, 5, cloth);
      fine(c, 3.5, 9, 9, 1, lighten(cloth, 0.3));
      fine(c, 3, 13, 10, 1, darken(cloth, 0.35));
    } else if (face === "up") {
      fine(c, 3, 12, 10, 6, darken(cloth, 0.2));
      fine(c, 4, 12.5, 8, 4, cloth);
      fine(c, 4, 12.5, 8, 1, lighten(cloth, 0.25));
      fine(c, 7.5, 13.5, 1, 2.5, darken(cloth, 0.3)); // costura
    } else {
      fine(c, 3, 1, 10, 9, darken(cloth, 0.2));
      fine(c, 4, 1.5, 8, 7, cloth);
      fine(c, 4, 1.5, 8, 1, lighten(cloth, 0.3));
      fine(c, 5, 4, 6, 0.5, darken(cloth, 0.3));
    }
  });
}

function leafBlob(ctx: Ctx, x: number, y: number, r: number, base: string) {
  // folhagem redonda com luz em cima e sombra embaixo
  const lo = darken(base, 0.3);
  const hi = lighten(base, 0.25);
  fine(ctx, x - r + 1, y - r, 2 * r - 2, 2 * r, base);
  fine(ctx, x - r, y - r + 1, 2 * r, 2 * r - 2, base);
  fine(ctx, x - r + 1, y + r - 2, 2 * r - 2, 1.5, lo);
  fine(ctx, x - r + 1.5, y - r + 1, r, 1, hi);
  fine(ctx, x - r + 1, y - r + 1.5, 1, r - 1, hi);
  fine(ctx, x + r * 0.2, y - r * 0.2, 1, 1, hi);
}

function pot(ctx: Ctx, x: number, y: number, w: number, h: number, c: string) {
  fine(ctx, x + 1, y + 1, w - 2, h - 1, c);
  fine(ctx, x, y, w, 2, lighten(c, 0.2));
  fine(ctx, x, y + 2, w, 0.5, darken(c, 0.35));
  fine(ctx, x + w - 2.5, y + 2.5, 1.5, h - 3, darken(c, 0.25));
  fine(ctx, x + 1.5, y + 3, 1, h - 5, lighten(c, 0.3));
}

function drawPlant(ctx: Ctx, x: number, y: number, big: boolean, t: number) {
  const sway = Math.sin(t * 1.3 + x) * 0.5;
  if (big) {
    pot(ctx, x + 3, y + 7, 10, 9, "#b5653a");
    fine(ctx, x + 4, y + 7, 8, 1, "#4a3020"); // terra
    leafBlob(ctx, x + 8 + sway, y - 6, 6, "#2e8b4a");
    leafBlob(ctx, x + 4 + sway * 0.6, y, 4.5, "#277a40");
    leafBlob(ctx, x + 12 + sway * 0.6, y - 1, 4.5, "#2a8445");
    leafBlob(ctx, x + 8 + sway * 1.2, y - 11, 4, "#3aa55a");
  } else {
    pot(ctx, x + 4.5, y + 9, 7, 6, "#d0703a");
    leafBlob(ctx, x + 8 + sway, y + 5, 4.5, "#34a853");
    leafBlob(ctx, x + 6 + sway, y + 2, 3, "#43c065");
    leafBlob(ctx, x + 10.5 + sway, y + 2.5, 2.5, "#3bb35c");
    fine(ctx, x + 9 + sway, y + 1, 1.5, 1.5, "#f7dc6f"); // florzinha
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
            blit(ctx, deskImage(), px, py - 8);
            // objetos da mesa
            // papéis empilhados
            fine(ctx, px + 3.5, py - 0.5, 6, 4, "#d5d8dc");
            fine(ctx, px + 3, py - 1, 6, 4, "#fbfcfc");
            for (let i = 0; i < 3; i++) fine(ctx, px + 4, py + i * 1, 4, 0.5, "#aab7b8");
            if (p.owner === "strategist") {
              monitor(ctx, px + 8, py - 12);
              monitor(ctx, px + 25, py - 12);
              screenContent(ctx, "strategist", px + 8, py - 12, 16, 11, t, info);
              screenContent(ctx, "strategist2", px + 25, py - 12, 16, 11, t, info);
            } else {
              monitor(ctx, px + 16, py - 12);
              screenContent(ctx, p.owner || "", px + 16, py - 12, 16, 11, t, info);
              // caneca com café quentinho
              const mug = ({ news: "#e74c3c", schedule: "#f39c12", auditor: "#3498db", risk: "#ecf0f1", cashier: "#27ae60", infra: "#95a5a6" } as Record<string, string>)[p.owner || ""] || "#ecf0f1";
              fine(ctx, px + 38, py - 2, 4, 5, mug);
              fine(ctx, px + 38, py - 2, 4, 1, "#4e2a14");
              fine(ctx, px + 38, py - 1, 1, 3.5, lighten(mug, 0.35));
              fine(ctx, px + 42, py - 0.5, 1, 2.5, mug);
              if (info.running) for (let k = 0; k < 2; k++) fine(ctx, px + 39 + k * 1.5 + Math.sin(t * 3 + k) * 0.5, py - 4 - ((t * 4 + k * 2) % 4), 0.5, 1.5, "rgba(255,255,255,0.55)");
            }
            // teclado com teclas e mouse
            fine(ctx, px + 14, py + 3.5, 20, 4, "#2d3436");
            for (let r = 0; r < 3; r++) for (let k = 0; k < 9; k++) fine(ctx, px + 15 + k * 2, py + 4 + r * 1.1, 1.5, 0.75, "#566573");
            fine(ctx, px + 36, py + 4, 2.5, 3.5, "#2d3436");
            fine(ctx, px + 36.5, py + 4.5, 1.5, 1, "#566573");
            // plaquinha com o cargo
            const labels: Record<string, string> = { news: "NOTÍCIAS", schedule: "HORÁRIOS", strategist: "ESTRATÉGIA", auditor: "AUDITORIA", risk: "RISCO", cashier: "CAIXA", infra: "TI" };
            fine(ctx, px + 2, py + 9, 26, 5, "#3a2a1e");
            fine(ctx, px + 2.5, py + 9.5, 25, 4, "#2c3e50");
            pixelText(ctx, labels[p.owner || ""] || "", px + 15, py + 10, "#f7dc6f", 3, "center");
          },
        },
      ];
    case "chair":
      return [
        { sortY: py + 2, draw: (ctx) => blit(ctx, chairImage(p.face || "up", "seat"), px, py - 2) },
        { sortY: p.face === "up" ? py + 15 : py + 1, draw: (ctx) => blit(ctx, chairImage(p.face || "up", "back"), px, p.face === "up" ? py - 2 : py - 10) },
      ];
    case "managerDesk":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            // mesa executiva de madeira escura com tampo de couro
            fine(ctx, px, py - 6, 64, 14, "#5d4037");
            fine(ctx, px + 3, py - 4, 58, 9, "#3e5a3c");
            fine(ctx, px + 3, py - 4, 58, 0.5, "#5b7f58");
            fine(ctx, px, py - 6, 64, 1, "#8d6e63");
            fine(ctx, px, py + 8, 64, 8, "#4e342e");
            fine(ctx, px, py + 8, 64, 0.5, "#795548");
            for (const dx of [6, 50]) {
              fine(ctx, px + dx, py + 9.5, 8, 5, "#5d4037");
              fine(ctx, px + dx + 3, py + 11.5, 2, 1, "#d4ac0d");
            }
            // notebook aberto
            fine(ctx, px + 20, py - 10, 18, 10, "#bdc3c7");
            fine(ctx, px + 21, py - 9, 16, 7, "#1b2631");
            for (let i = 0; i < 3; i++) fine(ctx, px + 22, py - 8 + i * 2, 6 + ((i * 5 + Math.floor(t * 2)) % 7), 0.75, i ? "#5dade2" : "#2ecc71");
            fine(ctx, px + 19, py, 20, 1.5, "#95a5a6");
            // pasta, telefone, porta-retrato e caneca
            fine(ctx, px + 4, py - 4, 9, 6, "#fbfcfc");
            fine(ctx, px + 5, py - 3, 7, 0.5, "#aab7b8");
            fine(ctx, px + 5, py - 1.5, 5, 0.5, "#aab7b8");
            fine(ctx, px + 42, py - 6, 5, 6, "#d4ac0d");
            fine(ctx, px + 42.5, py - 5.5, 4, 4, "#85c1e9");
            fine(ctx, px + 50, py - 5, 5, 6, "#c0392b");
            fine(ctx, px + 50, py - 5, 5, 1, "#4e2a14");
            pixelText(ctx, "GERENTE", px + 32, py + 10, "#f7dc6f", 3, "center");
          },
        },
      ];
    case "meetingTable":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            fine(ctx, px + 2, py - 4, 76, 30, "#5d4037");
            fine(ctx, px + 4, py - 2, 72, 24, "#8d6e63");
            fine(ctx, px + 4, py - 2, 72, 1, "#a1887f");
            for (let i = 0; i < 8; i++) fine(ctx, px + 8 + ((i * 19) % 60), py + 1 + (i % 5) * 4, 8 + (i % 3) * 4, 0.5, "#7b5e57");
            fine(ctx, px + 2, py + 26, 76, 4, "#3e2723");
            // papéis, xícaras e o projetor no meio
            fine(ctx, px + 10, py + 4, 8, 6, "#fbfcfc");
            fine(ctx, px + 11, py + 5.5, 6, 0.5, "#aab7b8");
            fine(ctx, px + 60, py + 10, 8, 6, "#fbfcfc");
            fine(ctx, px + 61, py + 11.5, 6, 0.5, "#aab7b8");
            fine(ctx, px + 22, py + 14, 3, 3, "#ecf0f1");
            fine(ctx, px + 52, py + 4, 3, 3, "#ecf0f1");
            fine(ctx, px + 35, py + 5, 10, 9, "#1c2833");
            fine(ctx, px + 35, py + 5, 10, 1, "#566573");
            fine(ctx, px + 38, py + 8, 4, 4, "#34495e");
            if (Math.floor(t) % 2) fine(ctx, px + 39, py + 9, 2, 2, "#2ecc71");
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
            fine(ctx, px, py - 18, 32, 34, "#6e2c00");
            fine(ctx, px, py - 18, 32, 1.5, "#935116");
            fine(ctx, px + 30, py - 17, 2, 33, "#4a1d00");
            const colors = ["#c0392b", "#2980b9", "#27ae60", "#f39c12", "#8e44ad", "#16a085", "#d35400", "#f4d03f"];
            for (let row = 0; row < 3; row++) {
              const sy = py - 16 + row * 10;
              fine(ctx, px + 2, sy, 28, 8, "#3b1a0a");
              let bx = px + 3;
              for (let b = 0; bx < px + 28; b++) {
                const bw = 2.5 + ((b * 7 + row + p.x) % 3) * 0.5;
                const bh = 6 - ((b + row) % 3) * 0.5;
                const c = colors[(b * 3 + row * 2 + p.x) % colors.length];
                if ((b + row + p.x) % 7 === 6) {
                  // livro deitado
                  fine(ctx, bx, sy + 6, 5, 1.5, c);
                  bx += 5.5;
                  continue;
                }
                fine(ctx, bx, sy + 8 - bh, bw, bh, c);
                fine(ctx, bx, sy + 8 - bh, 0.5, bh, lighten(c, 0.3));
                fine(ctx, bx, sy + 9.5 - bh, bw, 0.5, "#f7dc6f");
                bx += bw + 0.5;
              }
              fine(ctx, px + 2, sy + 8, 28, 1, "#935116");
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
            const w = p.w * T;
            const base = "#b03a2e";
            fine(ctx, px - 2, py - 8, w + 4, 12, darken(base, 0.2));
            fine(ctx, px - 1, py - 7, w + 2, 1, lighten(base, 0.2));
            // almofadas do encosto
            for (let i = 0; i < p.w; i++) {
              fine(ctx, px + i * T + 1, py - 6, T - 2, 8, base);
              fine(ctx, px + i * T + 2, py - 5.5, T - 4, 1, lighten(base, 0.25));
              fine(ctx, px + i * T + 7.5, py - 3, 1, 1, darken(base, 0.35)); // botão do capitonê
            }
            // assento
            fine(ctx, px, py + 2, w, 12, base);
            for (let i = 0; i < p.w; i++) {
              fine(ctx, px + i * T + 1, py + 2.5, T - 2, 1, lighten(base, 0.22));
              if (i) fine(ctx, px + i * T - 0.5, py + 3, 1, 10, darken(base, 0.3));
            }
            fine(ctx, px, py + 13, w, 1, darken(base, 0.35));
            // braços
            for (const ax of [px - 4, px + w - 1]) {
              fine(ctx, ax, py - 2, 5, 16, darken(base, 0.15));
              fine(ctx, ax, py - 2, 5, 1.5, lighten(base, 0.2));
            }
          },
        },
      ];
    case "coffeeTable":
      return [
        {
          sortY: bottom,
          draw: (ctx, t) => {
            const w = p.w * T - 8;
            fine(ctx, px + 4, py + 2, w, 9, "#6d4c41");
            fine(ctx, px + 4, py + 2, w, 1, "#8d6e63");
            fine(ctx, px + 4, py + 11, w, 3, "#4e342e");
            fine(ctx, px + 5, py + 14, 1.5, 2, "#3e2723");
            fine(ctx, px + 4 + w - 2.5, py + 14, 1.5, 2, "#3e2723");
            // revistas, xícaras e um pratinho de biscoitos
            fine(ctx, px + 10, py + 3, 7, 5, "#fbfcfc");
            fine(ctx, px + 11, py + 4, 5, 2, "#e74c3c");
            fine(ctx, px + 23, py + 3.5, 8, 5, "#2980b9");
            fine(ctx, px + 24, py + 4.5, 6, 0.5, "#d6eaf8");
            fine(ctx, px + 35, py + 4, 6, 4, "#ecf0f1");
            for (let i = 0; i < 3; i++) fine(ctx, px + 35.5 + i * 1.8, py + 4.8, 1.5, 1.5, "#d68910");
            fine(ctx, px + 18.5, py + 4, 2.5, 2.5, "#ecf0f1");
            fine(ctx, px + 19, py + 4.3, 1.5, 0.75, "#6e2c00");
            fine(ctx, px + 19.5 + Math.sin(t * 3) * 0.4, py + 1.5 - ((t * 3) % 2), 0.5, 1.5, "rgba(255,255,255,0.5)");
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
            const base = "#2e86c1";
            fine(ctx, px, py - 6, 16, 10, darken(base, 0.2));
            fine(ctx, px + 1, py - 5, 14, 1, lighten(base, 0.2));
            fine(ctx, px + 1, py + 3, 14, 11, base);
            fine(ctx, px + 2, py + 3.5, 12, 1, lighten(base, 0.3));
            fine(ctx, px + 1, py + 13, 14, 1, darken(base, 0.35));
            fine(ctx, px - 1, py, 3, 12, darken(base, 0.12));
            fine(ctx, px + 14, py, 3, 12, darken(base, 0.12));
            fine(ctx, px - 1, py, 3, 1, lighten(base, 0.25));
            fine(ctx, px + 14, py, 3, 1, lighten(base, 0.25));
            fine(ctx, px + 1, py + 14, 1.5, 1.5, "#3e2723");
            fine(ctx, px + 13.5, py + 14, 1.5, 1.5, "#3e2723");
          },
        },
      ];
    case "trash":
      return [
        {
          sortY: bottom,
          draw: (ctx) => {
            fine(ctx, px + 5, py + 6, 6, 8, "#7f8c8d");
            for (let i = 0; i < 3; i++) fine(ctx, px + 6 + i * 1.7, py + 7, 0.5, 6.5, "#95a5a6");
            fine(ctx, px + 4, py + 5, 8, 1.5, "#aab7b8");
            fine(ctx, px + 6, py + 4, 2.5, 1.5, "#fbfcfc"); // papel amassado
          },
        },
      ];
    case "lamp":
      return [
        {
          sortY: bottom,
          draw: (ctx, _t, info) => {
            if (info.running) {
              ctx.fillStyle = "rgba(255, 236, 160, 0.12)";
              ctx.beginPath();
              ctx.ellipse(px + 8, py + 14, 14, 5, 0, 0, Math.PI * 2);
              ctx.fill();
            }
            fine(ctx, px + 7, py - 8, 2, 22, "#2c3e50");
            fine(ctx, px + 7, py - 8, 0.5, 22, "#566573");
            fine(ctx, px + 3, py - 15, 10, 8, info.running ? "#f9e79f" : "#b7950b");
            fine(ctx, px + 3, py - 15, 10, 1, info.running ? "#fef9e7" : "#d4ac0d");
            fine(ctx, px + 3, py - 8, 10, 1, info.running ? "#f4d03f" : "#9a7d0a");
            fine(ctx, px + 4.5, py + 13, 7, 2, "#2c3e50");
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
    ctx.fillStyle = "rgba(10, 15, 40, 0.2)";
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
