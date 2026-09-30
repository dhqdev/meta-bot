// A* em grade (4 direções) para os agentes desviarem das mesas.

export type Tile = [number, number];

export function findPath(grid: boolean[][], from: Tile, to: Tile): Tile[] {
  const rows = grid.length;
  const cols = grid[0].length;
  const key = (x: number, y: number) => y * cols + x;
  const passable = (x: number, y: number) => x >= 0 && y >= 0 && x < cols && y < rows && (grid[y][x] || (x === to[0] && y === to[1]));
  if (from[0] === to[0] && from[1] === to[1]) return [];
  const open: Array<{ x: number; y: number; f: number }> = [{ x: from[0], y: from[1], f: 0 }];
  const g = new Map<number, number>([[key(from[0], from[1]), 0]]);
  const came = new Map<number, number>();
  const h = (x: number, y: number) => Math.abs(x - to[0]) + Math.abs(y - to[1]);
  let guard = 0;
  while (open.length && guard++ < 4000) {
    let best = 0;
    for (let i = 1; i < open.length; i++) if (open[i].f < open[best].f) best = i;
    const cur = open.splice(best, 1)[0];
    if (cur.x === to[0] && cur.y === to[1]) {
      const path: Tile[] = [];
      let k = key(cur.x, cur.y);
      while (came.has(k)) {
        path.push([k % cols, Math.floor(k / cols)]);
        k = came.get(k)!;
      }
      return path.reverse();
    }
    const base = g.get(key(cur.x, cur.y))!;
    for (const [dx, dy] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      const nx = cur.x + dx;
      const ny = cur.y + dy;
      if (!passable(nx, ny)) continue;
      const nk = key(nx, ny);
      const cost = base + 1;
      if (cost < (g.get(nk) ?? Infinity)) {
        g.set(nk, cost);
        came.set(nk, key(cur.x, cur.y));
        open.push({ x: nx, y: ny, f: cost + h(nx, ny) });
      }
    }
  }
  return [];
}

/** Bloco livre mais próximo (se o destino estiver bloqueado). */
export function nearestFree(grid: boolean[][], t: Tile): Tile {
  if (grid[t[1]]?.[t[0]]) return t;
  for (let r = 1; r < 6; r++) {
    for (let dy = -r; dy <= r; dy++) {
      for (let dx = -r; dx <= r; dx++) {
        const x = t[0] + dx;
        const y = t[1] + dy;
        if (grid[y]?.[x]) return [x, y];
      }
    }
  }
  return t;
}
