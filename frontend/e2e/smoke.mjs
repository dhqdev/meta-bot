// Teste de fumaça de ponta a ponta: login, escritório desenhando, todas as telas e WebSocket.
// Uso: BASE_URL=http://127.0.0.1:4173 E2E_EMAIL=... E2E_PASSWORD=... node e2e/smoke.mjs
// (CHROMIUM_PATH aponta para um Chromium já instalado, se não quiser o do Playwright)
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const BASE = process.env.BASE_URL || "http://127.0.0.1:4173";
const EMAIL = process.env.E2E_EMAIL || "dono@example.com";
const PASSWORD = process.env.E2E_PASSWORD || "senhaForte123";
const OUT = new URL("./out/", import.meta.url).pathname;
mkdirSync(OUT, { recursive: true });

const failures = [];
const check = (ok, message) => {
  console.log(`${ok ? "✓" : "✗"} ${message}`);
  if (!ok) failures.push(message);
};

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(`console: ${m.text()}`);
});

try {
  await page.goto(BASE);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASSWORD);
  await page.click('button[type="submit"]');
  await page.waitForSelector("canvas", { timeout: 20000 });
  check(true, "login e escritório carregados");

  // O canvas do escritório precisa ter pixels variados (não pode estar em branco)
  await page.waitForTimeout(2500);
  const colors = await page.evaluate(() => {
    const c = document.querySelector("canvas");
    const ctx = c.getContext("2d");
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    const seen = new Set();
    for (let i = 0; i < data.length; i += 4 * 97) seen.add((data[i] << 16) | (data[i + 1] << 8) | data[i + 2]);
    return seen.size;
  });
  check(colors > 20, `escritório desenhado (${colors} cores)`);
  await page.screenshot({ path: `${OUT}office.png` });

  const pages = [
    ["/agentes", "A EQUIPE"],
    ["/daily", "DAILY"],
    ["/estrategias", "ESTRATÉGIAS"],
    ["/operacoes", "OPERAÇÕES"],
    ["/config", "CONFIGURAÇÕES"],
  ];
  for (const [path, title] of pages) {
    await page.click(`header a[href="${path}"]`);
    const ok = await page
      .getByRole("heading", { name: title, exact: true })
      .waitFor({ timeout: 15000 })
      .then(() => true)
      .catch(() => false);
    check(ok, `tela ${path}`);
    await page.waitForTimeout(800);
    await page.screenshot({ path: `${OUT}${path.slice(1)}.png` });
  }

  // Configurações: metas do dia no topo e a IA só com os modelos fixos do OpenRouter
  check(await page.getByText("Modelo de cada agente").isVisible(), "configurações de IA (OpenRouter, modelos fixos) visíveis");
  check(await page.getByText("Limite de perda do dia").first().isVisible(), "metas do dia nas configurações");
  check((await page.locator('input[placeholder^="sk-ant"]').count()) === 0, "sem opção de chave da Anthropic");
  check(await page.getByText("Os 10 pares da equipe").isVisible(), "os 10 pares fixos nas configurações");

  // Perfil rápido salva sozinho, mesmo saindo da tela logo depois (sem tocar em Salvar)
  await page.getByText("Conservador", { exact: true }).click();
  await page.click('header a[href="/agentes"]');
  await page.waitForTimeout(1500);
  const saved = await page.evaluate(async () => (await (await fetch("/api/settings")).json()).config);
  check(saved.daily_loss_limit === 1.5 && saved.daily_profit_target === 1 && saved.risk_per_trade_pct === 0.25, "perfil de metas do dia fica salvo ao sair da tela");

  // Daily: botão para fazer a reunião agora e relatório gerado
  await page.click('header a[href="/daily"]');
  await page.getByRole("button", { name: /Fazer (a daily|uma prévia) agora/ }).click();
  const dailyOk = await page
    .getByText(/Ajustes (para amanhã|sugeridos)/)
    .first()
    .waitFor({ timeout: 30000 })
    .then(() => true)
    .catch(() => false);
  check(dailyOk, "daily feita na hora, com relatório");
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}daily-report.png`, fullPage: true });

  // WebSocket conectado (sem o aviso "reconectando…") e a conversa da equipe no escritório
  await page.click('header a[href="/"]');
  await page.waitForTimeout(2000);
  check(!(await page.getByText("reconectando…").first().isVisible().catch(() => false)), "WebSocket conectado");
  check(await page.getByRole("button", { name: "Conversa", exact: true }).isVisible(), "aba Conversa no escritório");
  check(await page.getByText("Mercado e operações hoje").isVisible(), "painel de mercado e operações do dia no escritório");
  const daily = await page.getByText("hora da daily").first().isVisible().catch(() => false);
  check(daily, "mensagens da equipe na conversa");

  // PWA: manifesto e service worker publicados
  const manifest = await fetch(`${BASE}/manifest.webmanifest`).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  check(!!manifest && manifest.icons?.length >= 3 && manifest.display === "standalone", "manifesto do app (PWA)");
  const sw = await fetch(`${BASE}/sw.js`);
  check(sw.ok, "service worker publicado");

  // Celular: barra de navegação embaixo e nada vazando para os lados
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/", "/daily", "/config"]) {
    await page.click(`nav a[href="${path}"] >> visible=true`);
    await page.waitForTimeout(1200);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 1, `celular sem rolagem lateral em ${path} (${overflow}px)`);
    await page.screenshot({ path: `${OUT}mobile${path === "/" ? "-office" : path.replace("/", "-")}.png` });
  }

  // API protegida: sem cookie, 401
  const anon = await fetch(`${BASE}/api/system`);
  check(anon.status === 401, `API protegida sem login (HTTP ${anon.status})`);
} catch (e) {
  check(false, `erro inesperado: ${e.message}`);
  await page.screenshot({ path: `${OUT}erro.png` }).catch(() => {});
} finally {
  await browser.close();
}

check(errors.length === 0, `sem erros no console${errors.length ? `:\n  ${errors.join("\n  ")}` : ""}`);
if (failures.length) {
  console.error(`\n${failures.length} verificação(ões) falharam`);
  process.exit(1);
}
console.log("\nTudo certo.");
