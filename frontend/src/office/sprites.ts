// Personagens em pixel-art gerados por código (sem imagens externas), no estilo dos jogos de fazenda:
// cabeça grande, olhos com brilho, três tons de cor (luz, base e sombra) e contorno fino.
// O desenho é feito em 2× (32 × 52 px) e ocupa 16 × 26 px lógicos no escritório: com o mundo ampliado,
// cada "meio pixel" aparece nítido e dá para ver rosto, cabelo, roupa e expressão.

export type Dir = "down" | "up" | "left" | "right";
export type Pose = "stand" | "walk" | "sit" | "sitdown";
/** Expressão do rosto: feliz (parado), concentrado (trabalhando), preocupado (alerta), dormindo e comemorando. */
export type Mood = "happy" | "focus" | "worried" | "sleep" | "joy";

export interface Look {
  skin: string;
  hair: string;
  hairStyle: "short" | "long" | "bob" | "bun" | "curly" | "cap" | "bald";
  shirt: string;
  shirtDark: string;
  pants: string;
  shoes: string;
  eyes?: string;
  accessory?: "glasses" | "headset" | "tie" | "beard" | "watch" | "earrings";
  accent?: string;
}

export const LOOKS: Record<string, Look> = {
  news: { skin: "#f6c896", hair: "#a33b2f", hairStyle: "long", shirt: "#1abc9c", shirtDark: "#148f77", pants: "#2c3e50", shoes: "#3b2a22", eyes: "#2e7d32", accessory: "headset", accent: "#3a3f4b" },
  schedule: { skin: "#c68642", hair: "#22191a", hairStyle: "short", shirt: "#e67e22", shirtDark: "#b85f10", pants: "#b8a37a", shoes: "#4a3322", eyes: "#3e2723", accessory: "watch", accent: "#f1c40f" },
  strategist: { skin: "#ffe0bd", hair: "#f2c94c", hairStyle: "bob", shirt: "#8e44ad", shirtDark: "#6c3483", pants: "#34495e", shoes: "#2a2230", eyes: "#2b5fa8", accessory: "glasses", accent: "#5b3a29" },
  manager: { skin: "#e8b07a", hair: "#9aa5ab", hairStyle: "short", shirt: "#2c3e50", shirtDark: "#1b2631", pants: "#1b2631", shoes: "#111111", eyes: "#3e2723", accessory: "tie", accent: "#c0392b" },
  risk: { skin: "#8d5524", hair: "#1a1414", hairStyle: "bun", shirt: "#c0392b", shirtDark: "#922b21", pants: "#232323", shoes: "#2b1b1b", eyes: "#2b1b14", accessory: "earrings", accent: "#f1c40f" },
  cashier: { skin: "#f1c27d", hair: "#6e3b1f", hairStyle: "curly", shirt: "#27ae60", shirtDark: "#1e8449", pants: "#4a4a52", shoes: "#3b2a16", eyes: "#4e342e", accessory: "beard", accent: "#6e3b1f" },
  auditor: { skin: "#ffe0bd", hair: "#e8edf0", hairStyle: "long", shirt: "#2980b9", shirtDark: "#1f618d", pants: "#2c3e50", shoes: "#2a2230", eyes: "#455a64", accessory: "glasses", accent: "#6d4c41" },
  infra: { skin: "#c68642", hair: "#2d3436", hairStyle: "cap", shirt: "#7f8c8d", shirtDark: "#5d6d7e", pants: "#2c3e50", shoes: "#1f1f24", eyes: "#3e2723", accessory: undefined, accent: "#e74c3c" },
};

/** Tamanho do personagem em pixels lógicos do escritório (pés na base). */
export const SPRITE_W = 16;
export const SPRITE_H = 26;
const S = 2; // detalhe: pixels do desenho por pixel lógico
const W = SPRITE_W * S;
const H = SPRITE_H * S;

type Px = (x: number, y: number, w: number, h: number, c: string) => void;

const cache = new Map<string, HTMLCanvasElement>();

function makeCanvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  return [c, ctx];
}

// ------------------------------------------------------------- cores
function hex(c: string): [number, number, number] {
  const v = c.replace("#", "");
  const n = parseInt(v.length === 3 ? v.split("").map((x) => x + x).join("") : v, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
function mix(a: string, b: string, k: number): string {
  const [r1, g1, b1] = hex(a);
  const [r2, g2, b2] = hex(b);
  const f = (x: number, y: number) => Math.round(x + (y - x) * k).toString(16).padStart(2, "0");
  return `#${f(r1, r2)}${f(g1, g2)}${f(b1, b2)}`;
}
const light = (c: string, k = 0.28) => mix(c, "#ffffff", k);
const dark = (c: string, k = 0.3) => mix(c, "#1a1020", k);

interface Tone {
  hi: string;
  base: string;
  lo: string;
}
const tone = (c: string, hiK = 0.28, loK = 0.3): Tone => ({ hi: light(c, hiK), base: c, lo: dark(c, loK) });

// ------------------------------------------------------------- cabeça
// Cabeça: x 7..24 (18 px), y0..y0+17. Rosto voltado para baixo (frente), lados ou costas.
const HEAD_X = 7;
const HEAD_W = 18;
const HEAD_H = 17;

function headShape(px: Px, y0: number, skin: Tone) {
  // forma arredondada com sombra embaixo e no lado direito
  px(HEAD_X + 2, y0, HEAD_W - 4, 1, skin.base);
  px(HEAD_X + 1, y0 + 1, HEAD_W - 2, 1, skin.base);
  px(HEAD_X, y0 + 2, HEAD_W, HEAD_H - 5, skin.base);
  px(HEAD_X + 1, y0 + HEAD_H - 3, HEAD_W - 2, 2, skin.base);
  px(HEAD_X + 3, y0 + HEAD_H - 1, HEAD_W - 6, 1, skin.base);
  px(HEAD_X + HEAD_W - 2, y0 + 3, 2, HEAD_H - 6, skin.lo);
  px(HEAD_X + 2, y0 + HEAD_H - 2, HEAD_W - 4, 1, skin.lo);
  px(HEAD_X + 2, y0 + 2, 3, 2, skin.hi);
}

function face(px: Px, l: Look, y0: number, mood: Mood, blink: boolean, skin: Tone) {
  const eye = l.eyes || "#2b1d14";
  const ey = y0 + 9;
  const lx = HEAD_X + 4;
  const rx = HEAD_X + HEAD_W - 7;
  const brow = dark(l.hair === "#e8edf0" || l.hair === "#9aa5ab" ? "#8d8d8d" : l.hair, 0.15);
  const closed = blink || mood === "sleep";
  // sobrancelhas: o formato muda com a expressão
  if (mood === "worried") {
    px(lx, ey - 2, 1, 1, brow);
    px(lx + 1, ey - 3, 2, 1, brow);
    px(rx + 2, ey - 2, 1, 1, brow);
    px(rx, ey - 3, 2, 1, brow);
  } else if (mood === "focus") {
    px(lx, ey - 3, 2, 1, brow);
    px(lx + 2, ey - 2, 1, 1, brow);
    px(rx, ey - 2, 1, 1, brow);
    px(rx + 1, ey - 3, 2, 1, brow);
  } else {
    px(lx, ey - 3, 3, 1, brow);
    px(rx, ey - 3, 3, 1, brow);
  }
  if (closed) {
    // olhos fechados: risquinho curvo
    px(lx, ey + 1, 3, 1, eye);
    px(rx, ey + 1, 3, 1, eye);
    px(lx, ey, 1, 1, skin.lo);
    px(rx + 2, ey, 1, 1, skin.lo);
  } else if (mood === "joy") {
    // olhos sorrindo (^ ^)
    px(lx, ey + 1, 1, 1, eye);
    px(lx + 1, ey, 1, 1, eye);
    px(lx + 2, ey + 1, 1, 1, eye);
    px(rx, ey + 1, 1, 1, eye);
    px(rx + 1, ey, 1, 1, eye);
    px(rx + 2, ey + 1, 1, 1, eye);
  } else {
    // olho: branco, íris colorida, pupila e brilho
    for (const x of [lx, rx]) {
      px(x, ey, 3, 3, "#fbfbff");
      px(x + 1, ey, 2, 3, eye);
      px(x + 1, ey + 1, 1, 2, "#140c10");
      px(x + 2, ey, 1, 1, "#ffffff");
      px(x, ey + 3, 3, 1, skin.lo);
    }
    if (mood === "focus") {
      // pálpebra meio baixa: olhar concentrado na tela
      px(lx, ey, 3, 1, skin.lo);
      px(rx, ey, 3, 1, skin.lo);
    }
  }
  // nariz
  px(HEAD_X + 9, ey + 3, 1, 1, skin.lo);
  // bochechas
  if (mood === "happy" || mood === "joy") {
    px(lx - 1, ey + 4, 2, 1, mix(skin.base, "#ff6f7d", 0.45));
    px(rx + 2, ey + 4, 2, 1, mix(skin.base, "#ff6f7d", 0.45));
  }
  // boca
  const my = y0 + 15;
  const mx = HEAD_X + 7;
  const lip = "#7a2e2e";
  if (mood === "joy") {
    px(mx, my - 1, 5, 1, lip);
    px(mx + 1, my, 3, 1, "#c0392b");
    px(mx + 1, my - 1, 3, 1, "#ffffff");
  } else if (mood === "happy") {
    px(mx, my - 1, 1, 1, lip);
    px(mx + 1, my, 3, 1, lip);
    px(mx + 4, my - 1, 1, 1, lip);
  } else if (mood === "worried") {
    px(mx + 1, my - 1, 3, 1, lip);
    px(mx, my, 1, 1, lip);
    px(mx + 4, my, 1, 1, lip);
  } else if (mood === "sleep") {
    px(mx + 2, my - 1, 1, 1, lip);
  } else {
    px(mx + 1, my - 1, 3, 1, lip);
  }
}

function hair(px: Px, l: Look, dir: Dir, y0: number) {
  const h = tone(l.hair, 0.3, 0.32);
  const x0 = HEAD_X;
  const x1 = HEAD_X + HEAD_W; // exclusivo
  const style = l.hairStyle;
  const front = dir === "down";
  const back = dir === "up";
  const side = dir === "left" || dir === "right";
  const flip = dir === "right";
  // franja/topo comum
  const crown = () => {
    px(x0 + 2, y0 - 2, HEAD_W - 4, 1, h.base);
    px(x0, y0 - 1, HEAD_W, 2, h.base);
    px(x0 - 1, y0 + 1, HEAD_W + 2, 3, h.base);
    px(x0 + 3, y0 - 1, 5, 1, h.hi);
    px(x0 + 2, y0, 3, 1, h.hi);
  };
  if (style === "bald") {
    if (!front) px(x0, y0 + 6, HEAD_W, 3, h.base);
    return;
  }
  if (style === "cap") {
    const capC = tone(l.accent || "#e74c3c", 0.25, 0.3);
    px(x0, y0 - 2, HEAD_W, 6, capC.base);
    px(x0 + 1, y0 - 3, HEAD_W - 2, 1, capC.base);
    px(x0 + 3, y0 - 2, 5, 2, capC.hi);
    px(x0, y0 + 3, HEAD_W, 1, capC.lo);
    if (front) px(x0 - 1, y0 + 4, HEAD_W + 2, 2, capC.lo);
    else if (side) px(flip ? x1 - 2 : x0 - 4, y0 + 4, 8, 2, capC.lo);
    else px(x0, y0 + 4, HEAD_W, 8, h.base);
    if (!back) {
      px(x0 - 1, y0 + 5, 2, 4, h.base);
      px(x1 - 1, y0 + 5, 2, 4, h.base);
    }
    return;
  }
  crown();
  if (back) {
    // de costas: o cabelo cobre a cabeça toda
    px(x0 - 1, y0 + 1, HEAD_W + 2, HEAD_H - 4, h.base);
    px(x0 + 1, y0 + HEAD_H - 4, HEAD_W - 2, 2, h.lo);
    px(x0 + 3, y0 + 2, 4, 3, h.hi);
  }
  switch (style) {
    case "short":
      if (front) {
        px(x0 - 1, y0 + 4, 2, 4, h.base);
        px(x1 - 1, y0 + 4, 2, 4, h.base);
        px(x0 + 4, y0 + 4, 4, 1, h.base);
        px(x0 + 10, y0 + 4, 3, 1, h.lo);
      } else if (side) {
        px(flip ? x0 - 1 : x1 - 9, y0 + 1, 10, 8, h.base);
        px(flip ? x0 : x1 - 3, y0 + 9, 3, 2, h.lo);
      }
      break;
    case "long":
      if (front) {
        px(x0 - 2, y0 + 2, 3, 15, h.base);
        px(x1 - 1, y0 + 2, 3, 15, h.base);
        px(x0 - 2, y0 + 14, 3, 3, h.lo);
        px(x1 - 1, y0 + 14, 3, 3, h.lo);
        px(x0 + 3, y0 + 4, 5, 1, h.base);
        px(x0 + 10, y0 + 4, 4, 1, h.base);
      } else if (side) {
        px(flip ? x0 - 2 : x1 - 10, y0 + 1, 12, 17, h.base);
        px(flip ? x0 - 2 : x1 - 2, y0 + 12, 4, 6, h.lo);
      } else {
        px(x0 - 2, y0 + 8, HEAD_W + 4, 12, h.base);
        px(x0, y0 + 17, HEAD_W, 3, h.lo);
        px(x0 + 8, y0 + 4, 1, 14, h.lo);
      }
      break;
    case "bob":
      if (front) {
        px(x0 - 2, y0 + 2, 3, 11, h.base);
        px(x1 - 1, y0 + 2, 3, 11, h.base);
        px(x0 - 1, y0 + 11, 2, 2, h.lo);
        px(x1 - 1, y0 + 11, 2, 2, h.lo);
        px(x0 + 1, y0 + 4, HEAD_W - 2, 2, h.base);
        px(x0 + 3, y0 + 4, 4, 1, h.hi);
      } else if (side) {
        px(flip ? x0 - 2 : x1 - 10, y0 + 1, 12, 12, h.base);
        px(flip ? x0 + 6 : x1 - 10, y0 + 4, 4, 2, h.base);
      } else {
        px(x0 - 2, y0 + 6, HEAD_W + 4, 7, h.base);
        px(x0 - 1, y0 + 12, HEAD_W + 2, 1, h.lo);
      }
      break;
    case "bun":
      px(x0 + 5, y0 - 6, 8, 5, h.base);
      px(x0 + 6, y0 - 6, 3, 2, h.hi);
      px(x0 + 5, y0 - 2, 8, 1, h.lo);
      if (front) {
        px(x0 - 1, y0 + 4, 2, 5, h.base);
        px(x1 - 1, y0 + 4, 2, 5, h.base);
        px(x0 + 2, y0 + 4, 6, 1, h.base);
      } else if (side) {
        px(flip ? x0 - 1 : x1 - 9, y0 + 1, 10, 7, h.base);
      }
      break;
    case "curly":
      for (let i = 0; i < 6; i++) px(x0 - 2 + i * 4, y0 - 3 + (i % 2), 3, 3, i % 2 ? h.hi : h.base);
      if (front) {
        px(x0 - 2, y0 + 2, 3, 6, h.base);
        px(x1 - 1, y0 + 2, 3, 6, h.base);
        px(x0 - 2, y0 + 5, 2, 2, h.lo);
        px(x1, y0 + 5, 2, 2, h.lo);
      } else if (side) {
        px(flip ? x0 - 2 : x1 - 10, y0, 12, 9, h.base);
        px(flip ? x0 : x1 - 6, y0 + 3, 3, 2, h.hi);
      }
      break;
  }
}

function earsAndFace(px: Px, l: Look, dir: Dir, y0: number, mood: Mood, blink: boolean, skin: Tone) {
  if (dir === "down") {
    px(HEAD_X - 1, y0 + 8, 1, 4, skin.base);
    px(HEAD_X + HEAD_W, y0 + 8, 1, 4, skin.lo);
    face(px, l, y0, mood, blink, skin);
  } else if (dir === "left" || dir === "right") {
    const flip = dir === "right";
    const fx = (x: number, w = 1) => (flip ? HEAD_X + HEAD_W - x - w : HEAD_X + x);
    const eye = l.eyes || "#2b1d14";
    const ey = y0 + 9;
    // orelha
    px(fx(10, 3), y0 + 8, 3, 4, skin.lo);
    // nariz para fora do rosto
    px(fx(-1), ey + 2, 1, 2, skin.base);
    // olho de perfil
    if (blink || mood === "sleep") px(fx(2, 2), ey + 1, 2, 1, eye);
    else {
      px(fx(1, 3), ey, 3, 3, "#fbfbff");
      px(fx(1, 2), ey, 2, 3, eye);
      px(fx(1), ey + 1, 1, 2, "#140c10");
      px(fx(2), ey, 1, 1, "#ffffff");
    }
    px(fx(1, 3), ey - 3, 3, 1, dark(l.hair, 0.15));
    const lip = "#7a2e2e";
    if (mood === "worried") px(fx(1, 2), y0 + 14, 2, 1, lip);
    else {
      px(fx(1, 2), y0 + 14, 2, 1, lip);
      if (mood === "happy" || mood === "joy") px(fx(3), y0 + 13, 1, 1, lip);
    }
    if (mood === "happy" || mood === "joy") px(fx(3, 2), ey + 4, 2, 1, mix(skin.base, "#ff6f7d", 0.45));
  }
}

function accessories(px: Px, l: Look, dir: Dir, y0: number) {
  const a = l.accessory;
  const ac = l.accent || "#222";
  if (a === "glasses") {
    const ey = y0 + 8;
    if (dir === "down") {
      for (const x of [HEAD_X + 3, HEAD_X + HEAD_W - 8]) {
        px(x, ey, 5, 1, ac);
        px(x, ey + 4, 5, 1, ac);
        px(x, ey, 1, 5, ac);
        px(x + 4, ey, 1, 5, ac);
        px(x + 1, ey + 1, 1, 1, "rgba(255,255,255,0.55)");
      }
      px(HEAD_X + 8, ey + 1, 2, 1, ac);
    } else if (dir === "left" || dir === "right") {
      const x = dir === "left" ? HEAD_X : HEAD_X + HEAD_W - 5;
      px(x, ey, 5, 1, ac);
      px(x, ey + 4, 5, 1, ac);
      px(dir === "left" ? x + 4 : x, ey, 1, 5, ac);
      px(dir === "left" ? x + 5 : x - 5, ey + 1, 5, 1, ac);
    }
  }
  if (a === "beard" && dir !== "up") {
    const b = tone(ac, 0.2, 0.3);
    if (dir === "down") {
      px(HEAD_X + 1, y0 + 11, 2, 4, b.base);
      px(HEAD_X + HEAD_W - 3, y0 + 11, 2, 4, b.lo);
      px(HEAD_X + 2, y0 + 14, HEAD_W - 4, 3, b.base);
      px(HEAD_X + 6, y0 + 13, 6, 1, b.base);
      px(HEAD_X + 7, y0 + 14, 4, 1, "#7a2e2e");
      px(HEAD_X + 4, y0 + 16, HEAD_W - 8, 2, b.lo);
    } else {
      const flip = dir === "right";
      px(flip ? HEAD_X + 6 : HEAD_X, y0 + 12, 12, 5, b.base);
    }
  }
  if (a === "earrings" && dir === "down") {
    px(HEAD_X - 1, y0 + 12, 1, 2, ac);
    px(HEAD_X + HEAD_W, y0 + 12, 1, 2, ac);
  }
  if (a === "headset") {
    const hs = "#3a3f4b";
    px(HEAD_X - 1, y0 - 2, HEAD_W + 2, 1, hs);
    px(HEAD_X - 2, y0 - 1, 1, 8, hs);
    px(HEAD_X + HEAD_W + 1, y0 - 1, 1, 8, hs);
    if (dir !== "right") px(HEAD_X - 3, y0 + 6, 3, 4, "#5c6370");
    if (dir !== "left") px(HEAD_X + HEAD_W, y0 + 6, 3, 4, "#5c6370");
    if (dir === "down" || dir === "left") {
      px(HEAD_X - 1, y0 + 10, 1, 4, hs);
      px(HEAD_X, y0 + 14, 3, 1, hs);
      px(HEAD_X + 3, y0 + 14, 1, 1, "#e74c3c");
    }
  }
}

// ------------------------------------------------------------- corpo
function body(px: Px, l: Look, dir: Dir, frame: number, pose: Pose, top: number) {
  const shirt: Tone = { hi: light(l.shirt, 0.22), base: l.shirt, lo: l.shirtDark };
  const skin = tone(l.skin, 0.25, 0.25);
  const pants = tone(l.pants, 0.18, 0.3);
  const shoes = tone(l.shoes, 0.25, 0.3);
  const swing = pose === "walk" ? (frame === 1 ? 2 : frame === 3 ? -2 : 0) : 0;
  const side = dir === "left" || dir === "right";
  // pescoço
  px(13, top - 1, 6, 2, skin.lo);
  // tronco (ombros arredondados)
  px(9, top, 14, 1, shirt.base);
  px(8, top + 1, 16, 12, shirt.base);
  px(8, top + 1, 2, 11, shirt.hi);
  px(21, top + 1, 3, 12, shirt.lo);
  px(8, top + 12, 16, 1, shirt.lo);
  if (dir === "down") {
    // gola
    px(13, top, 6, 1, light(l.shirt, 0.45));
    px(14, top + 1, 4, 1, skin.lo);
    if (l.accessory === "tie") {
      px(13, top, 2, 2, "#ecf0f1");
      px(17, top, 2, 2, "#ecf0f1");
      const tie = tone(l.accent || "#c0392b", 0.25, 0.3);
      px(15, top + 1, 2, 2, tie.lo);
      px(15, top + 3, 2, 7, tie.base);
      px(15, top + 10, 2, 1, tie.lo);
      px(10, top + 2, 3, 9, shirt.lo); // paletó aberto
      px(19, top + 2, 3, 9, shirt.lo);
    } else {
      // botões e bolso
      px(16, top + 3, 1, 1, shirt.lo);
      px(16, top + 6, 1, 1, shirt.lo);
      px(16, top + 9, 1, 1, shirt.lo);
      px(18, top + 4, 3, 2, shirt.lo);
    }
  } else if (dir === "up") {
    px(10, top + 3, 12, 1, shirt.lo);
  }
  // cinto
  px(8, top + 13, 16, 1, dark(l.pants, 0.45));
  px(15, top + 13, 2, 1, "#c9a227");

  // braços
  const arm = (x: number, y: number, len: number, hand = true) => {
    px(x, y, 3, len, shirt.lo);
    px(x, y, 1, len, shirt.base);
    if (hand) {
      px(x, y + len, 3, 2, skin.base);
      px(x + 2, y + len, 1, 2, skin.lo);
    }
  };
  if (pose === "sit") {
    // de costas, digitando: braços para frente, mãos na mesa
    const typing = frame % 2;
    arm(5, top + 1, 7, false);
    arm(24, top + 1, 7, false);
    px(6, top + 7 + typing, 3, 2, skin.base);
    px(23, top + 8 - typing, 3, 2, skin.base);
  } else if (pose === "sitdown") {
    arm(5, top + 1, 8);
    arm(24, top + 1, 8);
  } else if (side) {
    const ax = dir === "left" ? 14 : 15;
    px(ax, top + 1 - swing / 2, 3, 9, shirt.lo);
    px(ax, top + 10 - swing / 2, 3, 2, skin.base);
  } else {
    arm(5, top + 1 + swing / 2, 10);
    arm(24, top + 1 - swing / 2, 10);
    if (l.accessory === "watch") {
      px(24, top + 9 - swing / 2, 3, 1, l.accent || "#f1c40f");
    }
  }
  if (pose === "sit") return;

  const legTop = top + 14;
  if (pose === "sitdown") {
    // sentado de frente: coxas para a frente, pés embaixo da cadeira
    px(9, legTop, 14, 4, pants.base);
    px(9, legTop, 14, 1, pants.hi);
    px(15, legTop + 1, 1, 3, pants.lo);
    px(10, legTop + 4, 5, 2, shoes.base);
    px(17, legTop + 4, 5, 2, shoes.base);
    return;
  }
  if (side) {
    const a = pose === "walk" ? (frame === 1 ? -3 : frame === 3 ? 3 : 0) : 0;
    const toe = dir === "left" ? -2 : 0;
    px(12 + a, legTop, 4, 9, pants.base);
    px(16 - a, legTop, 4, 9, pants.lo);
    px(12 + a + toe, legTop + 9, 6, 3, shoes.base);
    px(16 - a + toe, legTop + 9, 6, 3, shoes.lo);
    px(12 + a + toe, legTop + 9, 6, 1, shoes.hi);
  } else {
    const lu = pose === "walk" && frame === 1 ? 2 : 0;
    const ru = pose === "walk" && frame === 3 ? 2 : 0;
    px(9, legTop, 7, 9 - lu, pants.base);
    px(16, legTop, 7, 9 - ru, pants.lo);
    px(9, legTop, 2, 9 - lu, pants.hi);
    px(15, legTop + 2, 2, 7, dark(l.pants, 0.4));
    px(8, legTop + 9 - lu, 8, 3, shoes.base);
    px(16, legTop + 9 - ru, 8, 3, shoes.base);
    px(9, legTop + 9 - lu, 5, 1, shoes.hi);
    px(17, legTop + 9 - ru, 5, 1, shoes.hi);
  }
}

/** Sprite do personagem (32 × 52 px de desenho; ocupa SPRITE_W × SPRITE_H lógicos, pés na base). */
export function characterSprite(id: string, dir: Dir, frame: number, pose: Pose, mood: Mood = "happy", blink = false): HTMLCanvasElement {
  const key = `${id}|${dir}|${frame}|${pose}|${mood}|${blink ? 1 : 0}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const look = LOOKS[id] ?? LOOKS.infra;
  const [canvas, ctx] = makeCanvas(W, H);
  const px: Px = (x, y, w, h, c) => {
    ctx.fillStyle = c;
    ctx.fillRect(x, y, w, h);
  };
  const bob = pose === "walk" && (frame === 1 || frame === 3) ? -1 : 0;
  const faceDir: Dir = pose === "sit" ? "up" : pose === "sitdown" ? "down" : dir;
  const y0 = 6 + bob; // topo da cabeça (sobra espaço para coque e boné)
  const top = y0 + HEAD_H + 1; // ombros
  const skin = tone(look.skin, 0.22, 0.22);
  const drawHead = () => {
    headShape(px, y0, skin);
    earsAndFace(px, look, faceDir, y0, mood, blink, skin);
    hair(px, look, faceDir, y0);
    accessories(px, look, faceDir, y0);
  };
  if (faceDir === "up") {
    body(px, look, faceDir, frame, pose, top);
    drawHead();
  } else {
    body(px, look, faceDir, frame, pose, top);
    drawHead();
  }
  outline(ctx, W, H);
  cache.set(key, canvas);
  return canvas;
}

/** Contorno escuro de 1 px (do desenho) em volta do personagem: fica nítido em qualquer piso. */
function outline(ctx: CanvasRenderingContext2D, w: number, h: number) {
  const img = ctx.getImageData(0, 0, w, h);
  const a = img.data;
  const solid = (x: number, y: number) => x >= 0 && y >= 0 && x < w && y < h && a[(y * w + x) * 4 + 3] > 40;
  const edge: number[] = [];
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (solid(x, y)) continue;
      if (solid(x - 1, y) || solid(x + 1, y) || solid(x, y - 1) || solid(x, y + 1)) edge.push(x, y);
    }
  }
  ctx.fillStyle = "rgba(24,14,22,0.92)";
  for (let i = 0; i < edge.length; i += 2) ctx.fillRect(edge[i], edge[i + 1], 1, 1);
}

/** Retrato quadrado (rosto e ombros) para cartões e listas. */
export function portrait(id: string, size = 48): string {
  const [canvas, ctx] = makeCanvas(size, size);
  const sprite = characterSprite(id, "down", 0, "stand", "happy");
  // recorte: cabeça e ombros (topo do desenho até o peito)
  const crop = 24;
  const scale = Math.max(1, Math.floor(size / crop));
  const d = crop * scale;
  ctx.imageSmoothingEnabled = scale * crop > size;
  ctx.drawImage(sprite, (W - crop) / 2, 1, crop, crop, (size - d) / 2, (size - d) / 2 + 2, d, d);
  return canvas.toDataURL();
}
