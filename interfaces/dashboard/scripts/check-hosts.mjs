#!/usr/bin/env node
/**
 * Post-deploy host smoke check.
 *
 * Hits every public 4truck.us hostname and verifies it responds the
 * way nginx + the deployed bundles say it should:
 *
 *   • dash.4truck.us, fleet/dispatch/safety.4truck.us  → 200, dashboard SPA
 *   • app.4truck.us                                    → 200, mini-app SPA
 *   • api.4truck.us/health                             → 200, JSON {"status":"ok"}
 *   • bot.4truck.us/                                   → 404 (intentional — no
 *                                                       public root, only /webhook)
 *   • 4truck.us  (apex)                                → 200 (marketing page)
 *
 * Run after `make deploy` or from CI as a deploy-verification step:
 *
 *     node scripts/check-hosts.mjs              # full check
 *     node scripts/check-hosts.mjs --strict     # also fails on warnings
 *     node scripts/check-hosts.mjs --base dev.4truck.us  # alt environment
 *
 * Exit code 0 = all green, 1 = any host failed.  Output is a single
 * table per host so the result fits in a CI log without scrolling.
 *
 * "Expected to fail" is a first-class concept: persona subdomains
 * (fleet/dispatch/safety) might not have DNS yet — flagged as a
 * warning, not a hard error, unless --strict is passed.
 */

import { argv, exit } from 'node:process';

const args = new Set(argv.slice(2));
const STRICT = args.has('--strict');

const baseArg = argv.find((a, i) => argv[i - 1] === '--base');
const BASE_DOMAIN = baseArg ?? '4truck.us';

const HOSTS = [
  {
    host: `dash.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 200, contains: '<div id="root"' },
    label: 'Dashboard SPA',
  },
  {
    host: `fleet.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 200, contains: '<div id="root"' },
    label: 'Fleet persona subdomain',
    softFail: true, // DNS may not be set up yet
  },
  {
    host: `dispatch.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 200, contains: '<div id="root"' },
    label: 'Dispatch persona subdomain',
    softFail: true,
  },
  {
    host: `safety.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 200, contains: '<div id="root"' },
    label: 'Safety persona subdomain',
    softFail: true,
  },
  {
    host: `app.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 200, contains: '<div id="root"' },
    label: 'Mini-App SPA',
  },
  {
    host: `api.${BASE_DOMAIN}`,
    path: '/health',
    expect: { status: 200, contains: '"status"' },
    label: 'API health endpoint',
  },
  {
    host: `bot.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 404 },
    label: 'Bot webhook host (404 on root is correct)',
  },
  {
    host: BASE_DOMAIN,
    path: '/',
    expect: { status: 200 },
    label: 'Apex marketing host',
  },
  {
    host: `www.${BASE_DOMAIN}`,
    path: '/',
    expect: { status: 200, contains: '4truck' },
    label: 'www. alias (301 → apex, then 200)',
  },
];

const TIMEOUT_MS = 10_000;

async function check({ host, path, expect: exp, label, softFail }) {
  const url = `https://${host}${path}`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const started = Date.now();

  try {
    const res = await fetch(url, {
      redirect: 'follow',
      signal: controller.signal,
      headers: { 'User-Agent': 'check-hosts/1.0 (deploy verification)' },
    });
    const elapsed = Date.now() - started;
    const issues = [];

    if (exp.status && res.status !== exp.status) {
      issues.push(`status ${res.status} (expected ${exp.status})`);
    }
    if (exp.contains) {
      const body = await res.text();
      if (!body.includes(exp.contains)) {
        issues.push(`body missing ${JSON.stringify(exp.contains)}`);
      }
    }
    return {
      url, label, softFail, elapsed,
      ok: issues.length === 0,
      message: issues.length === 0 ? 'ok' : issues.join('; '),
    };
  } catch (err) {
    return {
      url, label, softFail,
      elapsed: Date.now() - started,
      ok: false,
      message: err.name === 'AbortError' ? `timeout after ${TIMEOUT_MS}ms` : err.message,
    };
  } finally {
    clearTimeout(timer);
  }
}

const results = await Promise.all(HOSTS.map(check));

const padHost = Math.max(...results.map((r) => r.url.length));
const padLabel = Math.max(...results.map((r) => r.label.length));

let hard = 0;
let soft = 0;

for (const r of results) {
  let mark;
  if (r.ok) mark = '✓';
  else if (r.softFail && !STRICT) { mark = '~'; soft++; }
  else { mark = '✗'; hard++; }
  const url = r.url.padEnd(padHost);
  const label = r.label.padEnd(padLabel);
  const ms = `${r.elapsed}ms`.padStart(6);
  console.log(`${mark}  ${url}  ${label}  ${ms}  ${r.message}`);
}

console.log('');
if (hard === 0 && soft === 0) {
  console.log('All hosts healthy.');
  exit(0);
}
if (hard === 0) {
  console.log(`${soft} soft failure(s) — likely DNS not yet provisioned (rerun with --strict to fail on these).`);
  exit(0);
}
console.error(`${hard} host(s) failed.`);
exit(1);
