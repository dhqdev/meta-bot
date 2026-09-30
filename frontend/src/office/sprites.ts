// Personagens em pixel-art gerados por código (sem imagens externas).
// Cada sprite tem 16x24 px lógicos; o escritório desenha tudo ampliado sem suavização.

export type Dir = "down" | "up" | "left" | "right";
export type Pose = "stand" | "walk" | "sit" | "sitdown";

export interface Look {
  skin: string;
  hair: string;
  hairStyle: "short" | "long" | "bob" | "bun" | "curly" | "cap" | "bald";
  shirt: string;
  shirtDark: string;
  pants: string;
  shoes: string;
  accessory?: "glasses" | "headset" | "tie" | "beard" | "watch" | "earrings";
  accent?: string;
}

export const LOOKS: Record<string, Look> = {
  news: { skin: "#f1c27d", hair: "#8b2e2e", hairStyle: "long", shirt: "#1abc9c", shirtDark: "#148f77", pants: "#2c3e50", shoes: "#1b1b1b", accessory: "headset", accent: "#333" },
  schedule: { skin: "#c68642", hair: "#1c1c1c", hairStyle: "short", shirt: "#e67e22", shirtDark: "#b85f10", pants: "#b8a37a", shoes: "#3b2a1a", accessory: "watch", accent: "#f1c40f" },
  strategist: { skin: "#ffdbac", hair: "#f4d03f", hairStyle: "bob", shirt: "#8e44ad", shirtDark: "#6c3483", pants: "#34495e", shoes: "#1b1b1b", accessory: "glasses", accent: "#222" },
  manager: { skin: "#e0ac69", hair: "#8e9aa0", hairStyle: "short", shirt: "#2c3e50", shirtDark: "#1b2631", pants: "#1b2631", shoes: "#0b0b0b", accessory: "tie", accent: "#c0392b" },
  risk: { skin: "#8d5524", hair: "#141414", hairStyle: "bun", shirt: "#c0392b", shirtDark: "#922b21", pants: "#1b1b1b", shoes: "#1b1b1b", accessory: "earrings", accent: "#f1c40f" },
  cashier: { skin: "#f1c27d", hair: "#6e3b1f", hairStyle: "curly", shirt: "#27ae60", shirtDark: "#1e8449", pants: "#3d3d3d", shoes: "#2b1d0e", accessory: "beard", accent: "#6e3b1f" },
  auditor: { skin: "#ffdbac", hair: "#dfe6e9", hairStyle: "long", shirt: "#2980b9", shirtDark: "#1f618d", pants: "#2c3e50", shoes: "#1b1b1b", accessory: "glasses", accent: "#333" },
  infra: { skin: "#c68642", hair: "#2d3436", hairStyle: "cap", shirt: "#7f8c8d", shirtDark: "#5d6d7e", pants: "#2c3e50", shoes: "#111", accessory: undefined, accent: "#e74c3c" },
};

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

function drawHead(px: Px, l: Look, dir: Dir, bob: number) {
  const y0 = 1 + bob;
  // cabeça 8x8
  px(4, y0 + 1, 8, 7, l.skin);
  if (dir === "down") {
    px(5, y0 + 4, 1, 1, "#1b1b1b");
    px(10, y0 + 4, 1, 1, "#1b1b1b");
    px(7, y0 + 6, 2, 1, "#a0522d");
  } else if (dir === "left") {
    px(5, y0 + 4, 1, 1, "#1b1b1b");
    px(3, y0 + 4, 1, 2, l.skin);
  } else if (dir === "right") {
    px(10, y0 + 4, 1, 1, "#1b1b1b");
    px(12, y0 + 4, 1, 2, l.skin);
  }
  // cabelo
  const h = l.hair;
  switch (l.hairStyle) {
    case "short":
      px(4, y0, 8, 2, h);
      if (dir === "up") px(4, y0, 8, 6, h);
      else if (dir === "left") px(8, y0, 4, 4, h);
      else if (dir === "right") px(4, y0, 4, 4, h);
      else {
        px(4, y0 + 2, 1, 2, h);
        px(11, y0 + 2, 1, 2, h);
      }
      break;
    case "long":
      px(3, y0, 10, 3, h);
      if (dir === "up") px(3, y0, 10, 11, h);
      else if (dir === "left") px(7, y0, 6, 10, h);
      else if (dir === "right") px(3, y0, 6, 10, h);
      else {
        px(3, y0 + 2, 1, 8, h);
        px(12, y0 + 2, 1, 8, h);
      }
      break;
    case "bob":
      px(3, y0, 10, 3, h);
      if (dir === "up") px(3, y0, 10, 8, h);
      else if (dir === "left") px(7, y0, 6, 7, h);
      else if (dir === "right") px(3, y0, 6, 7, h);
      else {
        px(3, y0 + 2, 1, 5, h);
        px(12, y0 + 2, 1, 5, h);
      }
      break;
    case "bun":
      px(4, y0, 8, 2, h);
      px(6, y0 - 2, 4, 2, h);
      if (dir === "up") px(4, y0, 8, 6, h);
      else if (dir === "left") px(8, y0, 4, 5, h);
      else if (dir === "right") px(4, y0, 4, 5, h);
      break;
    case "curly":
      px(3, y0 - 1, 10, 3, h);
      px(3, y0 + 1, 2, 3, h);
      px(11, y0 + 1, 2, 3, h);
      if (dir === "up") px(3, y0 - 1, 10, 7, h);
      break;
    case "cap":
      px(4, y0, 8, 3, h);
      if (dir === "down") px(3, y0 + 2, 10, 1, l.accent || h);
      else if (dir === "left") px(2, y0 + 2, 4, 1, l.accent || h);
      else if (dir === "right") px(10, y0 + 2, 4, 1, l.accent || h);
      else px(4, y0, 8, 5, h);
      break;
    case "bald":
      if (dir !== "down") px(4, y0 + 2, 1, 3, "#5d4037");
      break;
  }
  // acessórios do rosto
  if (dir === "down") {
    if (l.accessory === "glasses") {
      px(4, y0 + 4, 3, 1, l.accent || "#222");
      px(9, y0 + 4, 3, 1, l.accent || "#222");
      px(7, y0 + 4, 2, 1, l.accent || "#222");
    }
    if (l.accessory === "beard") px(5, y0 + 6, 6, 2, l.accent || l.hair);
    if (l.accessory === "earrings") {
      px(3, y0 + 5, 1, 1, l.accent || "#f1c40f");
      px(12, y0 + 5, 1, 1, l.accent || "#f1c40f");
    }
  }
  if (l.accessory === "headset") {
    px(3, y0 + 1, 1, 5, "#333");
    px(12, y0 + 1, 1, 5, "#333");
    px(4, y0 - 1, 8, 1, "#333");
    if (dir === "down" || dir === "left") px(2, y0 + 5, 2, 2, "#555");
  }
}

function drawBody(px: Px, l: Look, dir: Dir, frame: number, pose: Pose, bob: number) {
  const top = 9 + bob;
  const swing = pose === "walk" ? (frame === 1 ? 1 : frame === 3 ? -1 : 0) : 0;
  // tronco
  px(4, top, 8, 7, l.shirt);
  px(4, top + 6, 8, 1, l.shirtDark);
  if (l.accessory === "tie" && dir === "down") {
    px(7, top, 2, 1, "#ecf0f1");
    px(7, top + 1, 2, 5, l.accent || "#c0392b");
  }
  // braços
  if (pose === "sit") {
    // de costas, digitando: braços para frente
    px(3, top + 1, 1, 4, l.shirtDark);
    px(12, top + 1, 1, 4, l.shirtDark);
    const typing = frame % 2;
    px(3, top + 4 + typing, 1, 1, l.skin);
    px(12, top + 5 - typing, 1, 1, l.skin);
  } else if (pose === "sitdown") {
    px(3, top + 1, 1, 4, l.shirtDark);
    px(12, top + 1, 1, 4, l.shirtDark);
    px(4, top + 5, 2, 1, l.skin);
    px(10, top + 5, 2, 1, l.skin);
  } else if (dir === "left" || dir === "right") {
    const ax = dir === "left" ? 7 : 8;
    px(ax, top + 1 - swing, 1, 5, l.shirtDark);
    px(ax, top + 6 - swing, 1, 1, l.skin);
  } else {
    px(3, top + 1 + swing, 1, 5, l.shirtDark);
    px(12, top + 1 - swing, 1, 5, l.shirtDark);
    px(3, top + 6 + swing, 1, 1, l.skin);
    px(12, top + 6 - swing, 1, 1, l.skin);
    if (l.accessory === "watch") px(12, top + 5 - swing, 1, 1, l.accent || "#f1c40f");
  }
  if (pose === "sit" || pose === "sitdown") return;
  // pernas
  const legTop = top + 7;
  if (dir === "left" || dir === "right") {
    const a = pose === "walk" ? (frame === 1 ? -1 : frame === 3 ? 1 : 0) : 0;
    px(6 + a, legTop, 2, 5, l.pants);
    px(8 - a, legTop, 2, 5, l.pants);
    px(6 + a + (dir === "left" ? -1 : 0), legTop + 5, 3, 2, l.shoes);
    px(8 - a + (dir === "left" ? -1 : 0), legTop + 5, 3, 2, l.shoes);
  } else {
    const lu = pose === "walk" && frame === 1 ? 1 : 0;
    const ru = pose === "walk" && frame === 3 ? 1 : 0;
    px(5, legTop, 3, 5 - lu, l.pants);
    px(8, legTop, 3, 5 - ru, l.pants);
    px(5, legTop + 5 - lu, 3, 2, l.shoes);
    px(8, legTop + 5 - ru, 3, 2, l.shoes);
  }
}

/** Sprite 16x24 do personagem (pés na parte de baixo). */
export function characterSprite(id: string, dir: Dir, frame: number, pose: Pose): HTMLCanvasElement {
  const key = `${id}|${dir}|${frame}|${pose}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const look = LOOKS[id] ?? LOOKS.infra;
  const [canvas, ctx] = makeCanvas(16, 24);
  const px: Px = (x, y, w, h, c) => {
    ctx.fillStyle = c;
    ctx.fillRect(x, y, w, h);
  };
  const bob = pose === "walk" && (frame === 1 || frame === 3) ? -1 : 0;
  const faceDir: Dir = pose === "sit" ? "up" : pose === "sitdown" ? "down" : dir;
  if (faceDir === "up") {
    drawBody(px, look, faceDir, frame, pose, bob);
    drawHead(px, look, faceDir, bob);
  } else {
    drawHead(px, look, faceDir, bob);
    drawBody(px, look, faceDir, frame, pose, bob);
  }
  cache.set(key, canvas);
  return canvas;
}

/** Retrato quadrado (rosto e ombros) para cartões e listas. */
export function portrait(id: string, size = 48): string {
  const [canvas, ctx] = makeCanvas(size, size);
  const sprite = characterSprite(id, "down", 0, "stand");
  const scale = Math.floor(size / 16);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(sprite, 0, 0, 16, 16, (size - 16 * scale) / 2, 2, 16 * scale, 16 * scale);
  return canvas.toDataURL();
}
