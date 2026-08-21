/**
 * Perf-rig measurement pass — the budgets, measured the way the audit
 * measured them: PerformanceObserver (layout-shift, longtask, event
 * timing), 3 runs with spread, then the key gestures again at 4× CPU
 * throttle via CDP.  Runs ONLY against the local rig (127.0.0.1:8020).
 */
const { chromium } = require('playwright');
const fs = require('fs');

const EXE = process.env.HOME + '/.cache/ms-playwright/chromium_headless_shell-1208/chrome-headless-shell-linux64/chrome-headless-shell';
const TOKEN = fs.readFileSync(__dirname + '/rig_token.txt', 'utf8').trim();
const URL = 'http://127.0.0.1:8020/kpi/dispatch';

const OBSERVERS = `
  window.__cls = 0; window.__shifts = []; window.__tasks = []; window.__events = [];
  new PerformanceObserver((l) => l.getEntries().forEach((e) => {
    if (!e.hadRecentInput) { window.__cls += e.value; window.__shifts.push({v: +e.value.toFixed(4), t: Math.round(e.startTime)}); }
  })).observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver((l) => l.getEntries().forEach((e) =>
    window.__tasks.push({ d: Math.round(e.duration), t: Math.round(e.startTime) })
  )).observe({ type: 'longtask', buffered: true });
  new PerformanceObserver((l) => l.getEntries().forEach((e) =>
    window.__events.push({ n: e.name, d: Math.round(e.duration), t: Math.round(e.startTime) })
  )).observe({ type: 'event', durationThreshold: 16, buffered: true });
`;

const median = (a) => a.slice().sort((x, y) => x - y)[Math.floor(a.length / 2)];
const fmt = (a) => `${median(a)} (${Math.min(...a)}-${Math.max(...a)})`;

async function settle(page, tClick) {
  // Wait until 500ms pass with no new long task; settle = last task end.
  await page.waitForFunction((t0) => {
    const last = window.__tasks.filter((x) => x.t >= t0).pop();
    const lastEnd = last ? last.t + last.d : t0;
    return performance.now() - lastEnd > 500;
  }, tClick, { timeout: 20000 });
  return page.evaluate((t0) => {
    const after = window.__tasks.filter((x) => x.t >= t0);
    const last = after.pop();
    return {
      settle: last ? Math.round(last.t + last.d - t0) : 0,
      tasks: after.length + (last ? 1 : 0),
      worst: Math.max(0, ...window.__tasks.filter((x) => x.t >= t0).map((x) => x.d)),
    };
  }, tClick);
}

async function gesture(page, name, doIt) {
  const t0 = await page.evaluate(() => performance.now());
  await doIt();
  const s = await settle(page, t0);
  const inp = await page.evaluate((t) => {
    const ev = window.__events.filter((e) => e.t >= t - 50 && /click|pointer/.test(e.n));
    return ev.length ? Math.max(...ev.map((e) => e.d)) : 0;
  }, t0);
  return { name, inp, ...s };
}

async function loadPass(browser, { throttle = 1 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1306, height: 855 } });
  const page = await ctx.newPage();
  await page.addInitScript(`localStorage.setItem('jwt', ${JSON.stringify(TOKEN)}); ${OBSERVERS}`);
  const cdp = await ctx.newCDPSession(page);
  if (throttle > 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: throttle });

  const t0 = Date.now();
  await page.goto(URL, { waitUntil: 'commit' });
  await page.waitForFunction(() => document.querySelectorAll('section').length >= 12, null, { timeout: 30000 });
  const tSections = Date.now() - t0;
  await page.waitForSelector('[class*="bg-ok-bg"]', { timeout: 30000 });
  const tChips = Date.now() - t0;
  await page.waitForTimeout(1500);   // let stagger + late data finish
  const load = await page.evaluate(() => ({
    cls: +window.__cls.toFixed(3),
    shifts: window.__shifts.filter((s) => s.v >= 0.01),
    nodes: document.querySelectorAll('*').length,
    fcp: Math.round(performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? 0),
    longTasks: window.__tasks.filter((t) => t.d > 50).map((t) => t.d),
  }));
  return { ctx, page, tSections, tChips, load };
}

(async () => {
  const browser = await chromium.launch({ executablePath: EXE });
  const out = { load1x: [], gestures1x: {}, load4x: [], gestures4x: {} };

  for (const [label, throttle] of [['1x', 1], ['4x', 4]]) {
    const loads = [];
    const g = {};
    for (let i = 0; i < 3; i++) {
      const { ctx, page, tSections, tChips, load } = await loadPass(browser, { throttle });
      loads.push({ tSections, tChips, ...load });

      for (const [name, doIt] of [
        ['collapse-all', () => page.getByRole('button', { name: 'Collapse all' }).click()],
        ['expand-all',   () => page.getByRole('button', { name: 'Expand all' }).click()],
        ['board→sheet',  () => page.getByRole('button', { name: 'Sheet', exact: true }).first().click()],
        ['sheet→board',  () => page.getByRole('button', { name: 'Board', exact: true }).first().click()],
      ]) {
        try {
          const r = await gesture(page, name, doIt);
          (g[name] ??= []).push(r);
        } catch (e) {
          (g[name] ??= []).push({ name, error: e.message.split('\n')[0] });
        }
      }
      await ctx.close();
    }
    out['load' + label] = loads;
    out['gestures' + label] = g;
  }
  await browser.close();

  // ── report ──
  for (const label of ['1x', '4x']) {
    const loads = out['load' + label];
    console.log(`\n═══ CPU ${label} ═══`);
    console.log(`LOAD  time-to-sections: ${fmt(loads.map((l) => l.tSections))} ms · time-to-chips: ${fmt(loads.map((l) => l.tChips))} ms`);
    console.log(`      FCP: ${fmt(loads.map((l) => l.fcp))} ms · CLS: ${loads.map((l) => l.cls).join(' / ')} · nodes: ${loads[0].nodes}`);
    console.log(`      load long-tasks>50ms: ${loads.map((l) => '[' + l.longTasks.join(',') + ']').join(' ')}`);
    const shifts = loads[0].shifts.map((s) => `${s.v}@${s.t}ms`).join(' ');
    if (shifts) console.log(`      shifts≥0.01 (run 1): ${shifts}`);
    for (const [name, runs] of Object.entries(out['gestures' + label])) {
      const ok = runs.filter((r) => !r.error);
      if (!ok.length) { console.log(`GESTURE ${name}: FAILED ${runs[0].error}`); continue; }
      console.log(`GESTURE ${name.padEnd(12)} INP: ${fmt(ok.map((r) => r.inp))} ms · settle: ${fmt(ok.map((r) => r.settle))} ms · worst task: ${fmt(ok.map((r) => r.worst))} ms`);
    }
  }
})();
