#!/usr/bin/env node
/**
 * Universal interaction-performance runner — the agentic-repro
 * instrument of the ux-audit-performance-interaction skill.  (v1.6.0)
 *
 * NOT hardcoded to any page, project or stack: everything
 * target-specific arrives in a small JSON profile.  Prints the skill's
 * report tables to stdout and writes NOTHING to disk (the
 * disposable-measurement rule).  Needs the `playwright` npm package
 * and a Playwright Chromium in ~/.cache/ms-playwright (auto-detected,
 * or set CHROMIUM_PATH).
 *
 *   node measure.mjs profile.json
 *
 * Reading the numbers (honesty notes):
 * - SETTLE = interaction → the later of (last long task end, last DOM
 *   mutation) + 500ms quiet — so sub-50ms chunked commits ARE counted.
 *   On continuously-animating pages mutation-quiet never arrives; the
 *   runner falls back to long-task-only and prefixes the value with
 *   `~` (approximate, optimistic on chunked UIs).
 * - INP column = max event-timing duration ≥16ms scoped to the
 *   gesture; a printed 0 means "<16ms", not literally zero.
 * - CLS is summed over the whole observed window without session
 *   windowing — reads equal-or-higher than field CLS; use it for
 *   composition and change-vs-baseline, not spec-exact absolutes.
 *
 * Profile shape (all target-specific knowledge lives here):
 * {
 *   "baseUrl": "https://staging.example.test",
 *   "viewport": { "width": 1306, "height": 855 },        // optional
 *   "runs": 3,                                            // optional
 *   "throttle": [1, 4],                                   // optional
 *   "auth": { "type": "none" }                            // or:
 *         { "type": "localStorage", "key": "auth_token",
 *           "value": "...", "valueFile": "path" },        // one of value/valueFile
 *   "pages": [{
 *     "name": "board",
 *     "path": "/heavy-board",
 *     "ready": { "selector": "[data-row-loaded]" },       // or {"minCount": {"selector": "section", "count": 12}}
 *     "settleMs": 1500,                                   // optional post-ready wait
 *     "gestures": [
 *       { "name": "expand-all", "click": { "role": "button", "text": "Expand all" } },
 *       { "name": "open-row",   "click": { "selector": ".row:first-child" } },
 *       { "name": "scroll",     "scroll": { "pixels": 4000 } }
 *     ]
 *   }]
 * }
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { chromium } from 'playwright';

const profile = JSON.parse(fs.readFileSync(process.argv[2] ?? 'profile.json', 'utf8'));
const RUNS = profile.runs ?? 3;
const THROTTLES = profile.throttle ?? [1, 4];
const VIEWPORT = profile.viewport ?? { width: 1306, height: 855 };

function chromiumPath() {
  if (process.env.CHROMIUM_PATH) return process.env.CHROMIUM_PATH;
  const root = path.join(os.homedir(), '.cache', 'ms-playwright');
  const dirs = fs.existsSync(root)
    ? fs.readdirSync(root).filter((d) => d.startsWith('chromium')).sort()
    : [];
  for (const d of dirs.reverse()) {
    for (const rel of [
      'chrome-headless-shell-linux64/chrome-headless-shell',
      'chrome-linux/chrome',
    ]) {
      const p = path.join(root, d, rel);
      if (fs.existsSync(p)) return p;
    }
  }
  return undefined;   // let playwright resolve its own
}

function authInit() {
  const a = profile.auth ?? { type: 'none' };
  if (a.type === 'none') return '';
  if (a.type === 'localStorage') {
    const v = a.value ?? fs.readFileSync(a.valueFile, 'utf8').trim();
    return `localStorage.setItem(${JSON.stringify(a.key)}, ${JSON.stringify(v)});`;
  }
  throw new Error(`unknown auth.type ${a.type}`);
}

const OBSERVERS = `
  window.__cls = 0; window.__shifts = []; window.__tasks = []; window.__events = [];
  window.__lastMut = 0;
  new PerformanceObserver((l) => l.getEntries().forEach((e) => {
    if (!e.hadRecentInput) { window.__cls += e.value; window.__shifts.push({ v: +e.value.toFixed(4), t: Math.round(e.startTime) }); }
  })).observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver((l) => l.getEntries().forEach((e) =>
    window.__tasks.push({ d: Math.round(e.duration), t: Math.round(e.startTime) })
  )).observe({ type: 'longtask', buffered: true });
  new PerformanceObserver((l) => l.getEntries().forEach((e) =>
    window.__events.push({ n: e.name, d: Math.round(e.duration), t: Math.round(e.startTime) })
  )).observe({ type: 'event', durationThreshold: 16, buffered: true });
  (function attachMut() {
    if (document.documentElement) {
      new MutationObserver(() => { window.__lastMut = performance.now(); })
        .observe(document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
    } else { setTimeout(attachMut, 0); }
  })();
`;

const median = (a) => a.slice().sort((x, y) => x - y)[Math.floor(a.length / 2)];
const fmt = (a) => (a.length ? `${median(a)} (${Math.min(...a)}-${Math.max(...a)})` : '—');

async function waitReady(page, ready) {
  if (ready?.selector) return page.waitForSelector(ready.selector, { timeout: 30000 });
  if (ready?.minCount) {
    return page.waitForFunction(
      ({ selector, count }) => document.querySelectorAll(selector).length >= count,
      ready.minCount, { timeout: 30000 });
  }
  return page.waitForLoadState('load');
}

async function settle(page, t0) {
  // Quiet = 500ms with no long task AND no DOM mutation (skill's
  // "interaction → last commit/paint" definition).  Continuously
  // animating pages never reach mutation-quiet — fall back to
  // long-task-only and mark the number approximate.
  let mode = 'mut';
  try {
    await page.waitForFunction((t) => {
      const lastTask = window.__tasks.filter((x) => x.t >= t).pop();
      const taskEnd = lastTask ? lastTask.t + lastTask.d : t;
      const mutEnd = window.__lastMut >= t ? window.__lastMut : t;
      return performance.now() - Math.max(taskEnd, mutEnd) > 500;
    }, t0, { timeout: 20000 });
  } catch {
    mode = 'task-only';
    await page.waitForFunction((t) => {
      const last = window.__tasks.filter((x) => x.t >= t).pop();
      return performance.now() - (last ? last.t + last.d : t) > 500;
    }, t0, { timeout: 20000 });
  }
  return page.evaluate(({ t, mode }) => {
    const after = window.__tasks.filter((x) => x.t >= t);
    const lastTask = after.length ? after[after.length - 1] : null;
    const taskEnd = lastTask ? lastTask.t + lastTask.d : t;
    const mutEnd = (mode === 'mut' && window.__lastMut >= t) ? window.__lastMut : t;
    return {
      settle: Math.round(Math.max(taskEnd, mutEnd) - t),
      worst: Math.max(0, ...after.map((x) => x.d)),
      approx: mode !== 'mut',
    };
  }, { t: t0, mode });
}

async function runGesture(page, g) {
  const t0 = await page.evaluate(() => performance.now());
  if (g.click?.selector) await page.locator(g.click.selector).first().click();
  else if (g.click?.role) {
    await page.getByRole(g.click.role, { name: g.click.text, exact: g.click.exact ?? true })
      .first().click();
  } else if (g.scroll) {
    await page.mouse.wheel(0, g.scroll.pixels ?? 2000);
  } else throw new Error(`gesture ${g.name}: no click/scroll`);
  const s = await settle(page, t0);
  const inp = await page.evaluate((t) => {
    const ev = window.__events.filter((e) => e.t >= t - 50 && /click|pointer|wheel/.test(e.n));
    return ev.length ? Math.max(...ev.map((e) => e.d)) : 0;
  }, t0);
  return { inp, ...s };
}

const browser = await chromium.launch({ executablePath: chromiumPath() });
const cleanup = async () => { try { await browser.close(); } catch {} };
process.on('SIGINT', async () => { await cleanup(); process.exit(130); });

try {
  for (const pageDef of profile.pages) {
    console.log(`\n══════ PAGE ${pageDef.name} (${pageDef.path}) ══════`);
    for (const throttle of THROTTLES) {
      const loads = [];
      const gestures = {};
      for (let i = 0; i < RUNS; i++) {
        const ctx = await browser.newContext({ viewport: VIEWPORT });
        try {
          const page = await ctx.newPage();
          await page.addInitScript(authInit() + OBSERVERS);
          const cdp = await ctx.newCDPSession(page);
          if (throttle > 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: throttle });

          const t0 = Date.now();
          await page.goto(profile.baseUrl + pageDef.path, { waitUntil: 'commit' });
          await waitReady(page, pageDef.ready);
          const ready = Date.now() - t0;
          await page.waitForTimeout(pageDef.settleMs ?? 1500);
          loads.push({
            ready,
            ...await page.evaluate(() => ({
              cls: +window.__cls.toFixed(3),
              nodes: document.querySelectorAll('*').length,
              fcp: Math.round(performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0),
              longTasks: window.__tasks.filter((t) => t.d > 50).length,
            })),
          });
          for (const g of pageDef.gestures ?? []) {
            try {
              (gestures[g.name] ??= []).push(await runGesture(page, g));
            } catch (e) {
              (gestures[g.name] ??= []).push({ error: e.message.split('\n')[0] });
            }
          }
        } finally {
          await ctx.close();
        }
      }
      console.log(`— CPU ${throttle}× —`);
      console.log(`LOAD  ready: ${fmt(loads.map((l) => l.ready))} ms · FCP: ${fmt(loads.map((l) => l.fcp))} ms · CLS: ${loads.map((l) => l.cls).join('/')}` +
        ` · nodes: ${loads[0].nodes} · long-tasks>50ms: ${loads.map((l) => l.longTasks).join('/')}`);
      for (const [name, runs] of Object.entries(gestures)) {
        const ok = runs.filter((r) => !r.error);
        if (!ok.length) { console.log(`GESTURE ${name}: FAILED ${runs[0].error}`); continue; }
        const approx = ok.some((r) => r.approx) ? '~' : '';
        console.log(`GESTURE ${name.padEnd(14)} INP: ${fmt(ok.map((r) => r.inp))} ms · settle: ${approx}${fmt(ok.map((r) => r.settle))} ms · worst task: ${fmt(ok.map((r) => r.worst))} ms`);
      }
    }
  }
} finally {
  await cleanup();
}
