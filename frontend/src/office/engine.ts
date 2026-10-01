// Simulação do escritório: agentes andando, sentando, conversando, reuniões e efeitos.
//
// Desenho em duas camadas, para ficar nítido em qualquer tela:
// - mundo (pixel-art): desenhado numa escala inteira num canvas à parte e reduzido com suavização;
// - sobreposição (balões, nomes, valores, mensagens): desenhada direto na resolução real da tela,
//   com fonte legível em qualquer tamanho (não encolhe junto com o cenário).
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

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

interface BubbleBox extends Rect {
  headY: number;
  pad: number;
  lh: number;
  size: number;
}

interface Bubble {
  text: string;
  until: number;
  gold?: boolean;
  kind?: string;
  lines?: string[];
  fontKey?: string;
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
  queue: Array<{ text: string; dur: number; gold?: boolean; kind?: string }>;
  inMeeting: boolean;
  wanderUntil: number;
  nextWander: number;
  sparkUntil: number;
  pingUntil: number;
  phase: number;
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
}

interface FloatText {
  x: number;
  y: number;
  life: number;
  max: number;
  color: string;
  text: string;
}

interface Flight {
  from: string;
  to: string;
  t0: number;
  dur: number;
  color: string;
}

interface Wave {
  x: number;
  y: number;
  t0: number;
  color: string;
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
  title: string;
  started: number;
  phase: "gathering" | "talking" | "ending";
  idx: number;
  nextAt: number;
}

const SPEED = 46; // px lógicos por segundo
const now = () => performance.now() / 1000;
const KIND_COLOR: Record<string, string> = { alerta: "#e74c3c", pedido: "#f5b041", resposta: "#5dade2", comemoracao: "#2ecc71", daily: "#f7dc6f", info: "#aab7c4" };
const UI_FONT = '"Inter", system-ui, sans-serif';

export class OfficeEngine {
  grid = buildGrid();
  background = renderBackground();
  props: Drawable[] = buildDrawables();
  sims = new Map<string, Sim>();
  particles: Particle[] = [];
  floats: FloatText[] = [];
  flights: Flight[] = [];
  waves: Wave[] = [];
  pets: Pet[];
  meeting?: Meeting;
  selected?: string;
  hovered?: string;
  private k = 2; // escala inteira do mundo neste quadro
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
          pingUntil: 0,
          phase: Math.random() * Math.PI * 2,
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

  say(id: string, text: string, gold = false, kind?: string) {
    const sim = this.sims.get(id);
    if (!sim) return;
    const clean = text.length > 120 ? text.slice(0, 118) + "…" : text;
    const dur = Math.min(9, 3.2 + clean.length * 0.045);
    if (sim.bubble && sim.bubble.until > now()) sim.queue.push({ text: clean, dur, gold, kind });
    else sim.bubble = { text: clean, until: now() + dur, gold, kind };
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
      case "message":
        this.message(ev);
        break;
      case "meeting":
        this.startMeeting(ev);
        break;
      case "levelup": {
        const sim = this.sims.get(ev.agent);
        if (sim) {
          sim.sparkUntil = now() + 3;
          this.say(ev.agent, `⭐ Nível ${ev.level} em ${ev.name}!`, true);
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
          const y = caio ? caio.y - 30 : 15 * T;
          this.floats.push({ x, y, life: 0, max: 2.8, color: pnl >= 0 ? "#2ecc71" : "#e74c3c", text: `${pnl >= 0 ? "+" : ""}${pnl.toFixed(2).replace(".", ",")}` });
          if (pnl > 0) this.coins(14 * T, 16 * T, "#2ecc71", 10);
        }
        break;
    }
  }

  /** Conversa da equipe: balão de quem fala e um envelope voando até quem recebe. */
  private message(ev: Record<string, any>) {
    const kind = String(ev.kind || "info");
    const inMeetingNow = this.meeting && (ev.kind === "daily" || this.meeting.participants.includes(ev.sender));
    this.say(ev.sender, ev.text, kind === "comemoracao" || kind === "daily", kind);
    if (inMeetingNow) return;
    const color = KIND_COLOR[kind] ?? KIND_COLOR.info;
    const from = this.sims.get(ev.sender);
    if (!from) return;
    if (ev.recipient && ev.recipient !== "all" && this.sims.has(ev.recipient)) {
      this.flights.push({ from: ev.sender, to: ev.recipient, t0: now(), dur: 1.3, color });
    } else {
      this.waves.push({ x: from.x, y: from.y - 12, t0: now(), color });
    }
  }

  private startMeeting(ev: Record<string, any>) {
    const participants: string[] = (ev.participants || []).filter((p: string) => this.sims.has(p));
    const host: string = ev.host;
    this.meeting = { host, participants, lines: ev.lines || [], title: ev.title || "Reunião", started: now(), phase: "gathering", idx: 0, nextAt: 0 };
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
      if (arrived || t - m.started > 16) {
        m.phase = "talking";
        m.nextAt = t + 0.8;
      }
    } else if (m.phase === "talking" && t >= m.nextAt) {
      const line = m.lines[m.idx];
      if (line) {
        this.say(line.agent, line.text, m.title === "Daily");
        m.idx += 1;
        m.nextAt = t + Math.min(6, 2.6 + line.text.length * 0.03);
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
      if (sim.bubble && sim.bubble.until < t) {
        sim.bubble = undefined;
        const next = sim.queue.shift();
        if (next) sim.bubble = { text: next.text, until: t + next.dur, gold: next.gold, kind: next.kind };
      }
      this.ambient(sim, t);
    }
    this.updateMeeting(t);
    for (const p of this.particles) {
      p.life += dt;
      p.x += p.vx * dt;
      p.y += p.vy * dt;
      p.vy += 90 * dt;
    }
    this.particles = this.particles.filter((p) => p.life < p.max);
    for (const f of this.floats) {
      f.life += dt;
      f.y -= 12 * dt;
    }
    this.floats = this.floats.filter((f) => f.life < f.max);
    this.flights = this.flights.filter((f) => {
      if (t - f.t0 < f.dur) return true;
      const target = this.sims.get(f.to);
      if (target) target.pingUntil = t + 1.2;
      return false;
    });
    this.waves = this.waves.filter((w) => t - w.t0 < 1.4);
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
  /** Posição arredondada para a grade real do canvas (movimento suave e nítido). */
  private snap(v: number) {
    return Math.round(v * this.k) / this.k;
  }

  private sitOffset(sim: Sim) {
    return sim.pose === "sit" ? -5 : sim.pose === "sitdown" ? -6 : 0;
  }

  /** Mundo em pixel-art, na escala inteira ``k``. */
  renderWorld(ctx: CanvasRenderingContext2D, k: number) {
    this.k = k;
    const t = now();
    ctx.setTransform(k, 0, 0, k, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(this.background, 0, 0);
    renderWall(ctx, this.info, t);

    const items: Drawable[] = [...this.props];
    for (const sim of this.sims.values()) {
      const off = this.sitOffset(sim);
      items.push({ sortY: sim.y + off, draw: (c) => this.drawSim(c, sim, t, off) });
    }
    for (const pet of this.pets) items.push({ sortY: pet.y, draw: (c) => this.drawPet(c, pet, t) });
    items.sort((a, b) => a.sortY - b.sortY);
    for (const d of items) d.draw(ctx, t, this.info);

    for (const p of this.particles) {
      ctx.globalAlpha = Math.max(0, 1 - p.life / p.max);
      ctx.fillStyle = p.color;
      ctx.fillRect(this.snap(p.x), this.snap(p.y), p.size, p.size);
    }
    ctx.globalAlpha = 1;
    // ondas de aviso para toda a equipe
    for (const w of this.waves) {
      const age = (t - w.t0) / 1.4;
      ctx.strokeStyle = w.color;
      ctx.globalAlpha = 1 - age;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(w.x, w.y, 6 + age * 38, 3 + age * 16, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;
    renderLighting(ctx, this.info, t);
    for (const sim of this.sims.values()) this.drawStateIcon(ctx, sim, t);
  }

  private drawSim(ctx: CanvasRenderingContext2D, sim: Sim, t: number, sitOffset: number) {
    const walking = sim.pose === "walk";
    const frame = walking ? Math.floor(sim.walkT * 8) % 4 : sim.pose === "sit" && sim.backend.state === "working" ? Math.floor(t * 6) % 2 : 0;
    const sprite = characterSprite(sim.id, sim.dir, frame, sim.pose);
    // respiração: meio pixel para cima e para baixo quando parado em pé
    const breathe = !walking && sim.pose === "stand" ? Math.round((Math.sin(t * 2 + sim.phase) + 1) * 0.5) * 0.5 : 0;
    const x = this.snap(sim.x - 8);
    const y = this.snap(sim.y - 24 + sitOffset - breathe);
    if (sim.pose !== "sit") {
      ctx.fillStyle = "rgba(0,0,0,0.25)";
      ctx.beginPath();
      ctx.ellipse(this.snap(sim.x), this.snap(sim.y), 5, 1.6, 0, 0, Math.PI * 2);
      ctx.fill();
    }
    if (this.selected === sim.id || this.hovered === sim.id) {
      ctx.strokeStyle = this.selected === sim.id ? "#f1c40f" : "rgba(255,255,255,0.75)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.ellipse(sim.x, sim.y + sitOffset, 9, 3.4, 0, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.drawImage(sprite, x, y);
    if (sim.sparkUntil > t) {
      for (let i = 0; i < 6; i++) {
        const a = t * 4 + (i * Math.PI) / 3;
        ctx.fillStyle = i % 2 ? "#f1c40f" : "#fdfefe";
        ctx.fillRect(this.snap(sim.x + Math.cos(a) * 11), this.snap(sim.y - 12 + sitOffset + Math.sin(a) * 11), 2, 2);
      }
    }
    if (sim.pingUntil > t) {
      const k = 1 - (sim.pingUntil - t) / 1.2;
      ctx.strokeStyle = `rgba(247,220,111,${1 - k})`;
      ctx.beginPath();
      ctx.arc(sim.x, sim.y - 14 + sitOffset, 6 + k * 8, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  private drawStateIcon(ctx: CanvasRenderingContext2D, sim: Sim, t: number) {
    const headY = sim.y - 26 + this.sitOffset(sim);
    const st = sim.backend.state;
    if (st === "working") {
      const a = t * 4;
      ctx.fillStyle = "#f5b041";
      for (let i = 0; i < 4; i++) ctx.fillRect(this.snap(sim.x + 6 + Math.cos(a + (i * Math.PI) / 2) * 2), this.snap(headY + 2 + Math.sin(a + (i * Math.PI) / 2) * 2), 1, 1);
    } else if (st === "alert" || st === "error") {
      if (Math.floor(t * 3) % 2) {
        ctx.fillStyle = "#e74c3c";
        ctx.fillRect(this.snap(sim.x + 5), this.snap(headY - 3), 2, 5);
        ctx.fillRect(this.snap(sim.x + 5), this.snap(headY + 3), 2, 1);
      }
    } else if (st === "off") {
      const k = (t * 0.8) % 1;
      pixelText(ctx, "z", sim.x + 6 + k * 3, headY - k * 6, `rgba(236,240,241,${1 - k})`, 4);
    }
  }

  /** Balões, nomes, valores e mensagens na resolução real da tela (``scale`` = px do canvas por px lógico). */
  renderOverlay(ctx: CanvasRenderingContext2D, scale: number, dpr: number) {
    const t = now();
    ctx.setTransform(scale, 0, 0, scale, 0, 0);
    ctx.imageSmoothingEnabled = true;
    const css = (v: number) => (v * dpr) / scale; // px de tela → px lógicos
    // envelopes voando de quem manda para quem recebe
    for (const f of this.flights) {
      const a = this.sims.get(f.from);
      const b = this.sims.get(f.to);
      if (!a || !b) continue;
      const k = Math.min(1, (t - f.t0) / f.dur);
      const e = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const x = a.x + (b.x - a.x) * e;
      const y = a.y - 22 + (b.y - a.y) * e - Math.sin(Math.PI * e) * 28;
      const w = css(14);
      const h = css(10);
      ctx.fillStyle = "#fdfefe";
      ctx.strokeStyle = f.color;
      ctx.lineWidth = css(1.5);
      ctx.beginPath();
      ctx.roundRect(x - w / 2, y - h / 2, w, h, css(2));
      ctx.fill();
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(x - w / 2, y - h / 2);
      ctx.lineTo(x, y + css(1));
      ctx.lineTo(x + w / 2, y - h / 2);
      ctx.stroke();
    }
    // valores de operações fechadas
    for (const f of this.floats) {
      ctx.globalAlpha = Math.max(0, 1 - f.life / f.max);
      ctx.font = `700 ${css(15)}px ${UI_FONT}`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.lineWidth = css(3);
      ctx.strokeStyle = "rgba(10,12,20,0.85)";
      ctx.strokeText(f.text, f.x, f.y);
      ctx.fillStyle = f.color;
      ctx.fillText(f.text, f.x, f.y);
    }
    ctx.globalAlpha = 1;
    // placa da reunião
    if (this.meeting && this.meeting.phase !== "gathering") {
      const label = this.meeting.title === "Daily" ? "📋 DAILY EM ANDAMENTO" : `🗣️ ${this.meeting.title.toUpperCase()}`;
      ctx.font = `700 ${css(11)}px ${UI_FONT}`;
      const w = ctx.measureText(label).width + css(14);
      const x = 32 * T + 8 - w / 2;
      const y = css(6); // na parede do topo: os balões de quem fala na mesa não cobrem a placa
      ctx.fillStyle = this.meeting.title === "Daily" ? "rgba(247,220,111,0.95)" : "rgba(20,26,40,0.9)";
      ctx.beginPath();
      ctx.roundRect(x, y, w, css(20), css(10));
      ctx.fill();
      ctx.fillStyle = this.meeting.title === "Daily" ? "#1b2631" : "#f7dc6f";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(label, x + w / 2, y + css(10));
    }
    // nomes (hover/seleção) e balões
    for (const sim of this.sims.values()) {
      const off = this.sitOffset(sim);
      if (this.hovered === sim.id || this.selected === sim.id) this.drawNameTag(ctx, sim, css, off);
    }
    // de baixo para cima: quem está mais acima sobe o balão se ele fosse cobrir o de outro agente
    const speaking = [...this.sims.values()].filter((s) => s.bubble).sort((a, b) => b.y - a.y);
    const placed: Rect[] = [];
    const boxes = speaking.map((sim) => [sim, this.layoutBubble(ctx, sim, css, placed)] as const);
    // pinta de cima para baixo: o balão de baixo cobre o bico do de cima
    for (const [sim, box] of boxes.reverse()) this.paintBubble(ctx, sim, box, css, t);
  }

  private drawNameTag(ctx: CanvasRenderingContext2D, sim: Sim, css: (v: number) => number, off: number) {
    const label = `${sim.name} · ${sim.role}`;
    ctx.font = `600 ${css(11)}px ${UI_FONT}`;
    const w = ctx.measureText(label).width + css(12);
    const h = css(18);
    const x = Math.max(2, Math.min(WIDTH - w - 2, sim.x - w / 2));
    const y = sim.y + off + css(4);
    ctx.fillStyle = "rgba(15,20,32,0.92)";
    ctx.beginPath();
    ctx.roundRect(x, y, w, h, css(9));
    ctx.fill();
    ctx.fillStyle = "#f7dc6f";
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    ctx.fillText(label, x + css(6), y + h / 2);
  }

  private layoutBubble(ctx: CanvasRenderingContext2D, sim: Sim, css: (v: number) => number, placed: Rect[]): BubbleBox {
    const b = sim.bubble!;
    const size = css(12);
    const fontKey = `${size.toFixed(3)}`;
    ctx.font = `500 ${size}px ${UI_FONT}`;
    const maxW = Math.min(css(220), WIDTH * 0.42);
    if (!b.lines || b.fontKey !== fontKey) {
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
      if (lines.length > 4) {
        lines.length = 4;
        lines[3] = lines[3].replace(/\s*\S*$/, "") + "…";
      }
      b.lines = lines;
      b.fontKey = fontKey;
    }
    const lines = b.lines;
    const pad = css(7);
    const lh = size * 1.3;
    const w = Math.min(maxW, Math.max(...lines.map((l) => ctx.measureText(l).width))) + pad * 2;
    const h = lines.length * lh + pad * 1.4;
    const headY = sim.y - 27 + this.sitOffset(sim);
    let bx = Math.max(2, Math.min(WIDTH - w - 2, sim.x - w / 2));
    let by = Math.max(2, headY - h - css(8));
    const gap = css(4);
    const hit = () => placed.find((r) => bx < r.x + r.w + gap && bx + w + gap > r.x && by < r.y + r.h + gap && by + h + gap > r.y);
    for (let r = hit(), tries = 0; r && tries < 6; r = hit(), tries++) {
      if (r.y - h - gap >= 2) by = r.y - h - gap;
      else bx = r.x + r.w + gap + w <= WIDTH - 2 ? r.x + r.w + gap : Math.max(2, r.x - w - gap); // sem espaço em cima: vai para o lado
    }
    placed.push({ x: bx, y: by, w, h });
    return { x: bx, y: by, w, h, headY, pad, lh, size };
  }

  private paintBubble(ctx: CanvasRenderingContext2D, sim: Sim, box: BubbleBox, css: (v: number) => number, t: number) {
    const b = sim.bubble!;
    const lines = b.lines ?? [b.text];
    const { x: bx, y: by, w, h, headY, pad, lh, size } = box;
    ctx.font = `500 ${size}px ${UI_FONT}`;
    const fade = Math.min(1, (b.until - t) / 0.35);
    ctx.globalAlpha = Math.max(0, fade);
    const accent = b.gold ? "#d4ac0d" : b.kind ? KIND_COLOR[b.kind] ?? "#2c3e50" : "#2c3e50";
    ctx.fillStyle = b.gold ? "#fef9e7" : "#fdfefe";
    ctx.strokeStyle = accent;
    ctx.lineWidth = css(1.5);
    ctx.beginPath();
    ctx.roundRect(bx, by, w, h, css(8));
    ctx.fill();
    ctx.stroke();
    // bico apontando para a cabeça
    const tipX = Math.max(bx + css(10), Math.min(bx + w - css(10), sim.x));
    ctx.beginPath();
    ctx.moveTo(tipX - css(5), by + h - css(0.5));
    ctx.lineTo(sim.x, headY - css(2));
    ctx.lineTo(tipX + css(5), by + h - css(0.5));
    ctx.closePath();
    ctx.fill();
    ctx.beginPath();
    ctx.moveTo(tipX - css(5), by + h);
    ctx.lineTo(sim.x, headY - css(2));
    ctx.lineTo(tipX + css(5), by + h);
    ctx.stroke();
    // nome em cima e texto
    ctx.fillStyle = b.gold ? "#7d6608" : "#1b2631";
    ctx.textAlign = "left";
    ctx.textBaseline = "top";
    lines.forEach((l, i) => ctx.fillText(l, bx + pad, by + pad * 0.7 + i * lh));
    ctx.globalAlpha = 1;
  }

  private drawPet(ctx: CanvasRenderingContext2D, pet: Pet, t: number) {
    const x = this.snap(pet.x);
    const y = this.snap(pet.y);
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
      const off = this.sitOffset(sim);
      if (x >= sim.x - 9 && x <= sim.x + 9 && y >= sim.y - 26 + off && y <= sim.y + off + 3 && sim.y > bestY) {
        best = sim.id;
        bestY = sim.y;
      }
    }
    return best;
  }
}

export const OFFICE_SIZE = { width: WIDTH, height: HEIGHT, cols: COLS, rows: ROWS };
