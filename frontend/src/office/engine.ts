// Simulação do escritório: agentes andando, sentando, conversando, reuniões e efeitos.
import { buildGrid, COLS, DESK_SPOTS, HEIGHT, LOCATION_SPOTS, MEETING_HOST, ROWS, T, WANDER, WIDTH, tileCenter, type Spot } from "./map";
import { findPath, nearestFree, type Tile } from "./path";
import { buildDrawables, pixelText, renderBackground, renderLighting, renderWall, type Drawable, type OfficeInfo } from "./scenery";
import { characterSprite, type Dir, type Pose } from "./sprites";

export interface AgentView {
  id: string;
  name: string;
  role: string;
  state: string;
  location: string;
  task: string;
  status_emoji?: string;
}

interface Bubble {
  text: string;
  until: number;
  gold?: boolean;
}

interface Sim {
  id: string;
  name: string;
  role: string;
  x: number;
  y: number;
  path: Tile[];
  spot: Spot;
  dir: Dir;
  pose: Pose;
  walkT: number;
  backend: AgentView;
  bubble?: Bubble;
  queue: Array<{ text: string; dur: number; gold?: boolean }>;
  inMeeting: boolean;
  wanderUntil: number;
  nextWander: number;
  sparkUntil: number;
}

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  life: number;
  max: number;
  color: string;
  size: number;
  text?: string;
}

interface Pet {
  kind: "roomba" | "cat";
  x: number;
  y: number;
  path: Tile[];
  dir: Dir;
  restUntil: number;
  home: Tile;
}

interface Meeting {
  host: string;
  participants: string[];
  lines: Array<{ agent: string; text: string }>;
  started: number;
  phase: "gathering" | "talking" | "ending";
  idx: number;
  nextAt: number;
}

const SPEED = 46; // px lógicos por segundo
const now = () => performance.now() / 1000;

export class OfficeEngine {
  grid = buildGrid();
  background = renderBackground();
  props: Drawable[] = buildDrawables();
  sims = new Map<string, Sim>();
  particles: Particle[] = [];
  pets: Pet[];
  meeting?: Meeting;
  selected?: string;
  hovered?: string;
  info: OfficeInfo = {
    running: false,
    mode: "paper",
    source: "synthetic",
    mt5: {},
    plan: [],
    equity: [],
    killSwitch: false,
    dayBlocked: false,
    sessions: [],
    hour: 12,
    lastTradeAt: 0,
  };

  constructor() {
    this.pets = [
      { kind: "roomba", x: 26 * T + 8, y: 22 * T + 12, path: [], dir: "left", restUntil: now() + 8, home: [26, 22] },
      { kind: "cat", x: 33 * T + 8, y: 21 * T + 12, path: [], dir: "left", restUntil: now() + 20, home: [33, 21] },
    ];
  }

  // ------------------------------------------------------------ agentes
  syncAgents(list: AgentView[]) {
    for (const a of list) {
      const sim = this.sims.get(a.id);
      if (!sim) {
        const spot = this.resolve(a.id, a.location || "desk");
        const [x, y] = tileCenter(spot.x, spot.y);
        this.sims.set(a.id, {
          id: a.id,
          name: a.name,
          role: a.role,
          x,
          y,
          path: [],
          spot,
          dir: spot.face,
          pose: spot.pose === "stand" ? "stand" : spot.pose,
          walkT: 0,
          backend: a,
          queue: [],
          inMeeting: false,
          wanderUntil: 0,
          nextWander: now() + 20 + Math.random() * 60,
          sparkUntil: 0,
        });
      } else {
        this.updateAgent(a);
      }
    }
  }

  updateAgent(a: AgentView) {
    const sim = this.sims.get(a.id);
    if (!sim) return this.syncAgents([a]);
    const moved = a.location !== sim.backend.location || a.state !== sim.backend.state;
    sim.backend = { ...sim.backend, ...a };
    if (moved && !sim.inMeeting) {
      sim.wanderUntil = 0;
      this.goTo(sim, this.resolve(sim.id, a.location || "desk"));
    }
  }

  private occupied(spot: Spot, except: string): boolean {
    for (const s of this.sims.values()) if (s.id !== except && s.spot.x === spot.x && s.spot.y === spot.y) return true;
    return false;
  }

  private pick(list: Spot[], id: string): Spot {
    return list.find((s) => !this.occupied(s, id)) ?? list[0];
  }

  resolve(id: string, location: string): Spot {
    if (location === "desk" || (id === "manager" && location === "manager")) return DESK_SPOTS[id] ?? DESK_SPOTS.news;
    if (location.startsWith("agent:")) {
      const other = this.sims.get(location.slice(6));
      if (other) {
        const tx = Math.floor(other.x / T);
        const ty = Math.floor(other.y / T);
        const around: Tile[] = [
          [tx + 1, ty],
          [tx - 1, ty],
          [tx, ty + 1],
          [tx + 1, ty + 1],
          [tx - 1, ty + 1],
        ];
        for (const [x, y] of around) {
          if (this.grid[y]?.[x] && !this.occupied({ x, y, face: "up", pose: "stand" }, id)) {
            const face: Dir = x > tx ? "left" : x < tx ? "right" : "up";
            return { x, y, face, pose: "stand" };
          }
        }
      }
      return DESK_SPOTS[id] ?? DESK_SPOTS.news;
    }
    const list = LOCATION_SPOTS[location];
    if (list) return this.pick(list, id);
    return DESK_SPOTS[id] ?? DESK_SPOTS.news;
  }

  private goTo(sim: Sim, spot: Spot) {
    sim.spot = spot;
    const from: Tile = [Math.floor(sim.x / T), Math.floor(sim.y / T)];
    const start = nearestFree(this.grid, from);
    const path = findPath(this.grid, start, [spot.x, spot.y]);
    if (!path.length && (from[0] !== spot.x || from[1] !== spot.y)) {
      const [x, y] = tileCenter(spot.x, spot.y);
      sim.x = x;
      sim.y = y;
      sim.path = [];
      this.arrive(sim);
      return;
    }
    sim.path = path;
    if (!path.length) this.arrive(sim);
    else sim.pose = "walk";
  }

  private arrive(sim: Sim) {
    sim.dir = sim.spot.face;
    sim.pose = sim.spot.pose === "stand" ? "stand" : sim.spot.pose;
  }

  say(id: string, text: string, gold = false) {
    const sim = this.sims.get(id);
    if (!sim) return;
    const clean = text.length > 70 ? text.slice(0, 68) + "…" : text;
    const dur = Math.min(8, 3 + clean.length * 0.05);
    if (sim.bubble && sim.bubble.until > now()) sim.queue.push({ text: clean, dur, gold });
    else sim.bubble = { text: clean, until: now() + dur, gold };
    if (sim.queue.length > 3) sim.queue.shift();
  }

  // ------------------------------------------------------------- eventos
  handle(ev: Record<string, any>) {
    switch (ev.type) {
      case "agent":
        this.updateAgent(ev as AgentView);
        break;
      case "say":
        this.say(ev.agent, ev.text);
        break;
      case "meeting":
        this.startMeeting(ev);
        break;
      case "levelup": {
        const sim = this.sims.get(ev.agent);
        if (sim) {
          sim.sparkUntil = now() + 3;
          this.say(ev.agent, `NÍVEL ${ev.level}! ${ev.name}`, true);
        }
        break;
      }
      case "trade":
        if (ev.event === "opened") {
          this.info.lastTradeAt = Date.now() / 1000;
          this.coins(14 * T, 16 * T, "#f1c40f", 14);
        } else if (ev.event === "closed") {
          const pnl = Number(ev.trade?.pnl ?? 0);
          this.info.lastTradeAt = Date.now() / 1000;
          const caio = this.sims.get("cashier");
          const x = caio ? caio.x : 14 * T;
          const y = caio ? caio.y - 26 : 15 * T;
          this.floatText(x, y, `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2).replace(".", ",")}`, pnl >= 0 ? "#2ecc71" : "#e74c3c");
          if (pnl > 0) this.coins(14 * T, 16 * T, "#2ecc71", 10);
        }
        break;
    }
  }

  private startMeeting(ev: Record<string, any>) {
    const participants: string[] = (ev.participants || []).filter((p: string) => this.sims.has(p));
    const host: string = ev.host;
    this.meeting = { host, participants, lines: ev.lines || [], started: now(), phase: "gathering", idx: 0, nextAt: 0 };
    const hostSim = this.sims.get(host);
    if (hostSim) {
      hostSim.inMeeting = true;
      this.goTo(hostSim, MEETING_HOST);
    }
    participants.forEach((id, i) => {
      const sim = this.sims.get(id)!;
      sim.inMeeting = true;
      sim.wanderUntil = 0;
      this.goTo(sim, LOCATION_SPOTS.meeting[i % LOCATION_SPOTS.meeting.length]);
    });
  }

  private updateMeeting(t: number) {
    const m = this.meeting;
    if (!m) return;
    const members = [m.host, ...m.participants].map((id) => this.sims.get(id)).filter(Boolean) as Sim[];
    if (m.phase === "gathering") {
      const arrived = members.every((s) => s.path.length === 0);
      if (arrived || t - m.started > 14) {
        m.phase = "talking";
        m.nextAt = t + 0.6;
      }
    } else if (m.phase === "talking" && t >= m.nextAt) {
      const line = m.lines[m.idx];
      if (line) {
        this.say(line.agent, line.text);
        m.idx += 1;
        m.nextAt = t + 3.4;
      } else {
        m.phase = "ending";
        m.nextAt = t + 2.5;
      }
    } else if (m.phase === "ending" && t >= m.nextAt) {
      for (const s of members) {
        s.inMeeting = false;
        this.goTo(s, this.resolve(s.id, s.backend.location || "desk"));
      }
      this.meeting = undefined;
    }
  }

  private coins(x: number, y: number, color: string, n: number) {
    for (let i = 0; i < n; i++) {
      this.particles.push({ x, y, vx: (Math.random() - 0.5) * 50, vy: -30 - Math.random() * 40, life: 0, max: 1.2, color, size: 2 });
    }
  }

  private floatText(x: number, y: number, text: string, color: string) {
    this.particles.push({ x, y, vx: 0, vy: -12, life: 0, max: 2.6, color, size: 6, text });
  }

  // ------------------------------------------------------------ atualização
  update(dt: number) {
    const t = now();
    for (const sim of this.sims.values()) {
      if (sim.path.length) {
        const [tx, ty] = sim.path[0];
        const [gx, gy] = tileCenter(tx, ty);
        const dx = gx - sim.x;
        const dy = gy - sim.y;
        const dist = Math.hypot(dx, dy);
        const step = SPEED * dt;
        if (Math.abs(dx) > Math.abs(dy)) sim.dir = dx > 0 ? "right" : "left";
        else if (dy !== 0) sim.dir = dy > 0 ? "down" : "up";
        if (dist <= step) {
          sim.x = gx;
          sim.y = gy;
          sim.path.shift();
          if (!sim.path.length) this.arrive(sim);
        } else {
          sim.x += (dx / dist) * step;
          sim.y += (dy / dist) * step;
        }
        sim.walkT += dt;
      }
      // balões
      if (sim.bubble && sim.bubble.until < t) {
        sim.bubble = undefined;
        const next = sim.queue.shift();
        if (next) sim.bubble = { text: next.text, until: t + next.dur, gold: next.gold };
      }
      this.ambient(sim, t);
    }
    this.updateMeeting(t);
    for (const p of this.particles) {
      p.life += dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      if (!p.text) p.vy += 90 * dt;
    }
    this.particles = this.particles.filter((p) => p.life < p.max);
    for (const pet of this.pets) this.updatePet(pet, dt, t);
  }

  /** Vida ambiente: com o sistema desligado ficam no lounge; ligados e à toa, dão uma volta de vez em quando. */
  private ambient(sim: Sim, t: number) {
    if (sim.inMeeting || sim.path.length) return;
    const b = sim.backend;
    if (!this.info.running && b.state === "off") {
      if (t > sim.nextWander) {
        sim.nextWander = t + 20 + Math.random() * 40;
        const opts = ["lounge", "coffee", "window", "library", "lounge"];
        this.goTo(sim, this.resolve(sim.id, opts[Math.floor(Math.random() * opts.length)]));
      }
      return;
    }
    if (b.state !== "idle" || b.location !== "desk") return;
    if (sim.wanderUntil && t > sim.wanderUntil) {
      sim.wanderUntil = 0;
      sim.nextWander = t + 60 + Math.random() * 120;
      this.goTo(sim, this.resolve(sim.id, "desk"));
    } else if (!sim.wanderUntil && t > sim.nextWander) {
      const spot = WANDER[Math.floor(Math.random() * WANDER.length)];
      if (!this.occupied(spot, sim.id)) {
        sim.wanderUntil = t + 8 + Math.random() * 8;
        this.goTo(sim, spot);
      } else sim.nextWander = t + 15;
    }
  }

  private updatePet(pet: Pet, dt: number, t: number) {
    if (pet.path.length) {
      const [tx, ty] = pet.path[0];
      const gx = tx * T + 8;
      const gy = ty * T + 12;
      const dx = gx - pet.x;
      const dy = gy - pet.y;
      const dist = Math.hypot(dx, dy);
      const speed = pet.kind === "roomba" ? 18 : 30;
      if (Math.abs(dx) > Math.abs(dy)) pet.dir = dx > 0 ? "right" : "left";
      if (dist <= speed * dt) {
        pet.x = gx;
        pet.y = gy;
        pet.path.shift();
        if (!pet.path.length) pet.restUntil = t + (pet.kind === "cat" ? 25 + Math.random() * 40 : 3 + Math.random() * 5);
      } else {
        pet.x += (dx / dist) * speed * dt;
        pet.y += (dy / dist) * speed * dt;
      }
      return;
    }
    if (t < pet.restUntil) return;
    let target: Tile;
    if (pet.kind === "cat") {
      const spots: Tile[] = [pet.home, [15, 18], [31, 20], [24, 17], [10, 7], [20, 12]];
      target = spots[Math.floor(Math.random() * spots.length)];
    } else {
      target = Math.random() < 0.15 ? pet.home : [2 + Math.floor(Math.random() * 36), 2 + Math.floor(Math.random() * 20)];
    }
    target = nearestFree(this.grid, target);
    const from = nearestFree(this.grid, [Math.floor(pet.x / T), Math.floor(pet.y / T)]);
    pet.path = findPath(this.grid, from, target).slice(0, 40);
    if (!pet.path.length) pet.restUntil = t + 5;
  }

  // -------------------------------------------------------------- desenho
  render(ctx: CanvasRenderingContext2D, scale: number) {
    const t = now();
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.background, 0, 0);
    renderWall(ctx, this.info, t);

    const items: Drawable[] = [...this.props];
    for (const sim of this.sims.values()) {
      const sitOffset = sim.pose === "sit" ? -5 : sim.pose === "sitdown" ? -6 : 0;
      items.push({ sortY: sim.y + sitOffset, draw: (c) => this.drawSim(c, sim, t, sitOffset) });
    }
    for (const pet of this.pets) items.push({ sortY: pet.y, draw: (c) => this.drawPet(c, pet, t) });
    items.sort((a, b) => a.sortY - b.sortY);
    for (const d of items) d.draw(ctx, t, this.info);

    for (const p of this.particles) {
      const alpha = Math.max(0, 1 - p.life / p.max);
      ctx.globalAlpha = alpha;
      if (p.text) pixelText(ctx, p.text, p.x, p.y, p.color, p.size, "center");
      else {
        ctx.fillStyle = p.color;
        ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size);
      }
      ctx.globalAlpha = 1;
    }
    renderLighting(ctx, this.info, t);
    for (const sim of this.sims.values()) this.drawOverlay(ctx, sim, t);
  }

  private drawSim(ctx: CanvasRenderingContext2D, sim: Sim, t: number, sitOffset: number) {
    const frame = sim.pose === "walk" ? Math.floor(sim.walkT * 8) % 4 : sim.pose === "sit" && sim.backend.state === "working" ? Math.floor(t * 6) % 2 : 0;
    const sprite = characterSprite(sim.id, sim.dir, frame, sim.pose);
    const x = Math.round(sim.x - 8);
    const y = Math.round(sim.y - 24 + sitOffset);
    if (sim.pose !== "sit") {
      ctx.fillStyle = "rgba(0,0,0,0.22)";
      ctx.fillRect(Math.round(sim.x - 5), Math.round(sim.y - 1), 10, 2);
    }
    if (this.selected === sim.id || this.hovered === sim.id) {
      ctx.strokeStyle = this.selected === sim.id ? "#f1c40f" : "rgba(255,255,255,0.7)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(sim.x, sim.y + sitOffset, 8, 3, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.drawImage(sprite, x, y);
    if (sim.sparkUntil > t) {
      for (let i = 0; i < 6; i++) {
        const a = t * 4 + (i * Math.PI) / 3;
        ctx.fillStyle = i % 2 ? "#f1c40f" : "#fdfefe";
        ctx.fillRect(Math.round(sim.x + Math.cos(a) * 11), Math.round(sim.y - 12 + sitOffset + Math.sin(a) * 11), 2, 2);
      }
    }
  }

  private drawOverlay(ctx: CanvasRenderingContext2D, sim: Sim, t: number) {
    const sitOffset = sim.pose === "sit" ? -5 : sim.pose === "sitdown" ? -6 : 0;
    const headY = sim.y - 26 + sitOffset;
    const st = sim.backend.state;
    // ícone de estado
    if (st === "working") {
      const a = t * 4;
      ctx.fillStyle = "#f5b041";
      for (let i = 0; i < 4; i++) ctx.fillRect(Math.round(sim.x + 6 + Math.cos(a + (i * Math.PI) / 2) * 2), Math.round(headY + 2 + Math.sin(a + (i * Math.PI) / 2) * 2), 1, 1);
    } else if (st === "alert" || st === "error") {
      if (Math.floor(t * 3) % 2) {
        ctx.fillStyle = "#e74c3c";
        ctx.fillRect(Math.round(sim.x + 5), Math.round(headY - 3), 2, 5);
        ctx.fillRect(Math.round(sim.x + 5), Math.round(headY + 3), 2, 1);
      }
    } else if (st === "off") {
      const k = (t * 0.8) % 1;
      pixelText(ctx, "z", sim.x + 6 + k * 3, headY - k * 6, `rgba(236,240,241,${1 - k})`, 4);
    }
    if (this.hovered === sim.id || this.selected === sim.id) {
      const label = `${sim.name} · ${sim.role}`;
      ctx.font = '4px "Press Start 2P", monospace';
      const w = ctx.measureText(label).width + 6;
      ctx.fillStyle = "rgba(20,24,32,0.85)";
      ctx.fillRect(Math.round(sim.x - w / 2), Math.round(sim.y + 2), Math.round(w), 8);
      pixelText(ctx, label, sim.x, sim.y + 4, "#f7dc6f", 4, "center");
    }
    if (sim.bubble) this.drawBubble(ctx, sim.x, headY - 3, sim.bubble);
  }

  private drawBubble(ctx: CanvasRenderingContext2D, x: number, y: number, b: Bubble) {
    const size = 5;
    ctx.font = `${size}px "Press Start 2P", monospace`;
    const maxW = 118;
    const words = b.text.split(" ");
    const lines: string[] = [];
    let cur = "";
    for (const w of words) {
      const test = cur ? `${cur} ${w}` : w;
      if (ctx.measureText(test).width > maxW && cur) {
        lines.push(cur);
        cur = w;
      } else cur = test;
    }
    if (cur) lines.push(cur);
    const shown = lines.slice(0, 3);
    const w = Math.min(maxW, Math.max(...shown.map((l) => ctx.measureText(l).width))) + 8;
    const h = shown.length * (size + 3) + 5;
    let bx = Math.round(x - w / 2);
    bx = Math.max(2, Math.min(WIDTH - w - 2, bx));
    const by = Math.round(Math.max(2, y - h - 4));
    ctx.fillStyle = b.gold ? "#fef9e7" : "#fdfefe";
    ctx.strokeStyle = b.gold ? "#d4ac0d" : "#2c3e50";
    ctx.lineWidth = 1;
    ctx.fillRect(bx, by, w, h);
    ctx.strokeRect(bx + 0.5, by + 0.5, w - 1, h - 1);
    ctx.fillRect(Math.round(x) - 2, by + h - 1, 4, 3);
    ctx.fillStyle = ctx.strokeStyle;
    ctx.fillRect(Math.round(x) - 1, by + h + 2, 2, 2);
    shown.forEach((l, i) => pixelText(ctx, l, bx + 4, by + 4 + i * (size + 3), b.gold ? "#7d6608" : "#1b2631", size));
  }

  private drawPet(ctx: CanvasRenderingContext2D, pet: Pet, t: number) {
    const x = Math.round(pet.x);
    const y = Math.round(pet.y);
    if (pet.kind === "roomba") {
      ctx.fillStyle = "rgba(0,0,0,0.25)";
      ctx.fillRect(x - 5, y, 10, 2);
      ctx.fillStyle = "#566573";
      ctx.fillRect(x - 5, y - 4, 10, 5);
      ctx.fillStyle = "#85929e";
      ctx.fillRect(x - 4, y - 5, 8, 2);
      ctx.fillStyle = Math.floor(t * 2) % 2 ? "#2ecc71" : "#1d8348";
      ctx.fillRect(x - 1, y - 5, 2, 1);
      return;
    }
    // gato Pixel
    const moving = pet.path.length > 0;
    const flip = pet.dir === "right" ? 1 : -1;
    ctx.fillStyle = "#e67e22";
    ctx.fillRect(x - 5, y - 5, 10, 5);
    ctx.fillRect(x + flip * 4 - 2, y - 8, 5, 5);
    ctx.fillStyle = "#d35400";
    ctx.fillRect(x + flip * 4 - 2, y - 9, 1, 1);
    ctx.fillRect(x + flip * 4 + 2, y - 9, 1, 1);
    const tail = Math.round(Math.sin(t * 3) * 1.5);
    ctx.fillRect(x - flip * 6 - 1, y - 7 + tail, 2, 4);
    if (moving) {
      const leg = Math.floor(t * 8) % 2;
      ctx.fillRect(x - 4, y, 2, 1 + leg);
      ctx.fillRect(x + 2, y, 2, 2 - leg);
    } else {
      ctx.fillStyle = "#1b1b1b";
      ctx.fillRect(x + flip * 4 - 1, y - 6, 1, 1);
      if (Math.floor(t / 3) % 2) pixelText(ctx, "z", x + 4, y - 16 - ((t * 3) % 4), "rgba(255,255,255,0.8)", 4);
    }
  }

  hitTest(x: number, y: number): string | undefined {
    let best: string | undefined;
    let bestY = -1;
    for (const sim of this.sims.values()) {
      const off = sim.pose === "sit" ? -5 : sim.pose === "sitdown" ? -6 : 0;
      if (x >= sim.x - 8 && x <= sim.x + 8 && y >= sim.y - 24 + off && y <= sim.y + off + 2 && sim.y > bestY) {
        best = sim.id;
        bestY = sim.y;
      }
    }
    return best;
  }
}

export const OFFICE_SIZE = { width: WIDTH, height: HEIGHT, cols: COLS, rows: ROWS };
