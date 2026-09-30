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
    ["/estrategias", "ESTRATÉGIAS"],
    ["/mercado", "MERCADO"],
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

  // Configurações: a tela de IA mostra os modelos econômicos do OpenRouter
  const aiOk = await page.getByText("Modelo de cada agente").isVisible();
  check(aiOk, "configurações de IA (OpenRouter/Anthropic) visíveis");

  // WebSocket conectado (sem o aviso "reconectando…")
  await page.click('header a[href="/"]');
  await page.waitForTimeout(2000);
  check(!(await page.getByText("reconectando…").first().isVisible().catch(() => false)), "WebSocket conectado");

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
