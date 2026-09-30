// Planta do escritório (visão de cima): 40 x 24 blocos de 16 px.
import type { Dir } from "./sprites";

export const T = 16;
export const COLS = 40;
export const ROWS = 24;
export const WIDTH = COLS * T;
export const HEIGHT = ROWS * T;

export type SpotPose = "stand" | "sit" | "sitdown";
export interface Spot {
  x: number;
  y: number;
  face: Dir;
  pose: SpotPose;
}

export type PropKind =
  | "desk"
  | "chair"
  | "managerDesk"
  | "meetingTable"
  | "plant"
  | "bigPlant"
  | "rack"
  | "cooling"
  | "safe"
  | "counter"
  | "bookshelf"
  | "kitchen"
  | "coffee"
  | "fridge"
  | "cooler"
  | "sofa"
  | "coffeeTable"
  | "printer"
  | "arcade"
  | "armchair"
  | "trash"
  | "lamp"
  | "standTable";

export interface Prop {
  kind: PropKind;
  x: number;
  y: number;
  w: number;
  h: number;
  owner?: string;
  face?: Dir;
  blocks?: boolean;
}

const P = (kind: PropKind, x: number, y: number, w = 1, h = 1, extra: Partial<Prop> = {}): Prop => ({ kind, x, y, w, h, blocks: true, ...extra });

// Mesas dos agentes: a mesa ocupa 3 blocos e a cadeira fica logo abaixo (de costas para nós).
export const DESKS: Record<string, { x: number; y: number }> = {
  news: { x: 2, y: 4 },
  schedule: { x: 7, y: 4 },
  strategist: { x: 12, y: 4 },
  auditor: { x: 17, y: 4 },
  risk: { x: 2, y: 9 },
  cashier: { x: 7, y: 9 },
  infra: { x: 12, y: 9 },
};

export const PROPS: Prop[] = [
  ...Object.entries(DESKS).flatMap(([owner, d]) => [P("desk", d.x, d.y, 3, 1, { owner }), P("chair", d.x + 1, d.y + 1, 1, 1, { owner, face: "up", blocks: false })]),
  // sala do gerente
  P("managerDesk", 31, 4, 4, 1, { owner: "manager" }),
  P("chair", 32, 3, 1, 1, { owner: "manager", face: "down", blocks: false }),
  P("meetingTable", 30, 8, 5, 2),
  P("bigPlant", 37, 3),
  P("bigPlant", 28, 3),
  P("lamp", 37, 9),
  // área aberta
  P("bigPlant", 1, 2),
  P("plant", 22, 4),
  P("standTable", 23, 7, 2, 1),
  P("printer", 17, 9, 2, 1),
  P("cooler", 20, 9),
  P("plant", 25, 12),
  P("trash", 16, 5, 1, 1),
  P("trash", 6, 10, 1, 1),
  P("bigPlant", 1, 12),
  // sala dos servidores (MT5)
  P("rack", 2, 15, 1, 2),
  P("rack", 3, 15, 1, 2),
  P("rack", 5, 15, 1, 2),
  P("rack", 6, 15, 1, 2),
  P("cooling", 8, 15, 2, 1),
  // cofre do Caixa
  P("safe", 13, 16, 2, 2),
  P("counter", 16, 17, 2, 1),
  // biblioteca (skills)
  P("bookshelf", 20, 15, 2, 1),
  P("bookshelf", 22, 15, 2, 1),
  P("armchair", 24, 18),
  P("plant", 19, 21),
  // copa e lounge
  P("kitchen", 32, 12, 5, 1),
  P("fridge", 37, 12),
  P("cooler", 29, 12),
  P("sofa", 30, 18, 3, 1, { face: "down" }),
  P("coffeeTable", 30, 20, 3, 1),
  P("armchair", 34, 19),
  P("arcade", 37, 19),
  P("bigPlant", 28, 21),
  P("bigPlant", 38, 16),
  P("plant", 38, 22),
];

// Paredes de vidro e divisórias (x, y de cada bloco bloqueado).
export const GLASS: Array<[number, number]> = [];
for (let y = 2; y <= 10; y++) if (y !== 7) GLASS.push([27, y]);
for (let x = 27; x <= 38; x++) if (x !== 32) GLASS.push([x, 11]);
export const PARTITION: Array<[number, number]> = [];
for (let x = 1; x <= 10; x++) if (x !== 9) PARTITION.push([x, 14]);

export const DOOR = { x: 18, w: 2 };

export const DESK_SPOTS: Record<string, Spot> = {
  ...Object.fromEntries(Object.entries(DESKS).map(([id, d]) => [id, { x: d.x + 1, y: d.y + 1, face: "up" as Dir, pose: "sit" as SpotPose }])),
  manager: { x: 32, y: 3, face: "down", pose: "sitdown" },
};

const S = (x: number, y: number, face: Dir = "up", pose: SpotPose = "stand"): Spot => ({ x, y, face, pose });

export const LOCATION_SPOTS: Record<string, Spot[]> = {
  tv: [S(4, 2), S(3, 2), S(5, 2)],
  whiteboard: [S(14, 2), S(13, 2), S(15, 2), S(12, 2), S(16, 2)],
  window: [S(20, 2), S(9, 2), S(21, 2)],
  server: [S(3, 17), S(6, 17), S(4, 18)],
  vault: [S(13, 18), S(14, 18), S(16, 18)],
  library: [S(21, 16), S(23, 16), S(22, 17)],
  coffee: [S(33, 13), S(35, 13), S(29, 13), S(34, 14)],
  lounge: [S(30, 19, "down", "sitdown"), S(31, 19, "down", "sitdown"), S(32, 19, "down", "sitdown"), S(34, 20, "left"), S(36, 17, "left"), S(29, 17, "right"), S(33, 16, "down"), S(24, 19, "left")],
  manager: [S(32, 5), S(33, 5), S(31, 5)],
  meeting: [S(29, 8, "right"), S(35, 8, "left"), S(29, 9, "right"), S(35, 9, "left"), S(31, 10), S(33, 10)],
};
export const MEETING_HOST: Spot = S(32, 7, "down");

// Pontos para onde os agentes passeiam quando estão à toa.
export const WANDER: Spot[] = [S(33, 13), S(29, 13), S(20, 2), S(9, 2), S(21, 16), S(23, 10, "right"), S(10, 7, "left"), S(18, 12, "down")];

export function buildGrid(): boolean[][] {
  const walk: boolean[][] = Array.from({ length: ROWS }, (_, y) => Array.from({ length: COLS }, (_, x) => y >= 2 && y <= 22 && x >= 1 && x <= 38));
  for (const p of PROPS) {
    if (!p.blocks) continue;
    for (let dy = 0; dy < p.h; dy++) for (let dx = 0; dx < p.w; dx++) if (walk[p.y + dy]) walk[p.y + dy][p.x + dx] = false;
  }
  for (const [x, y] of [...GLASS, ...PARTITION]) walk[y][x] = false;
  for (let x = DOOR.x; x < DOOR.x + DOOR.w; x++) walk[22][x] = true;
  return walk;
}

export function tileCenter(x: number, y: number): [number, number] {
  return [x * T + T / 2, y * T + T - 2];
}
