/**
 * Sanitize + format AI response text for safe HTML rendering inside
 * the Telegram WebApp.  Mirrors interfaces/dashboard/src/utils/formatAI.ts
 * but uses inline styles instead of Tailwind classes (the miniapp
 * doesn't ship Tailwind).
 *
 * The Gemini/agent backend can return either:
 *   1. HTML markup (<p>, <b>, <ul>, …) — we sanitize dangerous bits
 *      and pass the rest through to ``dangerouslySetInnerHTML``.
 *   2. Plain text / markdown — we escape, then transform markdown
 *      to safe HTML (bold, italic, lists, tables, headings).
 *
 * Without this helper the chat bubble renders literal "<p>I can show
 * you …</p><b>107</b>" as visible text — see the driver-side bug
 * reported May 2026.
 */

const RE_DANGEROUS_TAGS = /<\/?(script|style|iframe|object|embed|form|input|textarea|select|button|link|meta|base)\b[^>]*>/gi;
const RE_EVENT_ATTRS = /\s+on\w+="[^"]*"/gi;
const RE_EVENT_ATTRS2 = /\s+on\w+='[^']*'/gi;
const RE_JS_HREF = /href\s*=\s*["']javascript:[^"']*["']/gi;

function looksLikeHTML(text: string): boolean {
  return /<(p|ul|ol|li|h[1-6]|div|br|strong|em|b|i|table|thead|tbody|tr|td|th)\b[^>]*>/i.test(text);
}

function sanitizeHTML(html: string): string {
  return html
    .replace(RE_DANGEROUS_TAGS, '')
    .replace(RE_EVENT_ATTRS, '')
    .replace(RE_EVENT_ATTRS2, '')
    .replace(RE_JS_HREF, '');
}

export function formatAIResponse(text: string): string {
  // Backend already returned HTML — sanitize + return.
  if (looksLikeHTML(text)) {
    return sanitizeHTML(text);
  }

  // Plain-text / markdown path: escape, then markdown → HTML.
  let s = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Inline: **bold**, <b>bold</b>, *italic*, <i>italic</i>
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/&lt;b&gt;(.+?)&lt;\/b&gt;/g, '<strong>$1</strong>');
  s = s.replace(/&lt;strong&gt;(.+?)&lt;\/strong&gt;/g, '<strong>$1</strong>');
  s = s.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
  s = s.replace(/&lt;i&gt;(.+?)&lt;\/i&gt;/g, '<em>$1</em>');

  // Block-level: headings, lists, tables.  No Tailwind here — the
  // miniapp wires styles via inline ``style=`` so we use minimal
  // attributes and let the parent bubble's inline style drive look.
  const lines = s.split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    const h3 = line.match(/^###\s+(.+)$/);
    if (h3) { out.push(`<h3>${h3[1]}</h3>`); i++; continue; }
    const h2 = line.match(/^##\s+(.+)$/);
    if (h2) { out.push(`<h2>${h2[1]}</h2>`); i++; continue; }

    // Tables: pipe-delimited rows.
    if (/^\|.+\|/.test(line)) {
      const tableLines: string[] = [];
      while (i < lines.length && /^\|.+\|/.test(lines[i])) {
        tableLines.push(lines[i]); i++;
      }
      const dataRows = tableLines.filter(r => !/^\|[-:| ]+\|$/.test(r));
      if (dataRows.length >= 2) {
        const [hdr, ...body] = dataRows;
        const ths = hdr.split('|').filter(Boolean)
          .map(c => `<th>${c.trim()}</th>`).join('');
        const trs = body.map(r =>
          `<tr>${r.split('|').filter(Boolean).map(c => `<td>${c.trim()}</td>`).join('')}</tr>`
        ).join('');
        out.push(`<table><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table>`);
      } else {
        tableLines.forEach(l => out.push(l + '<br/>'));
      }
      continue;
    }

    // Numbered lists.
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        const m = lines[i].match(/^\d+\.\s+(.+)$/);
        items.push(`<li>${m ? m[1] : lines[i]}</li>`);
        i++;
      }
      out.push(`<ol>${items.join('')}</ol>`);
      continue;
    }

    // Bullet lists.
    if (/^[•-]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[•-]\s+/.test(lines[i])) {
        const m = lines[i].match(/^[•-]\s+(.+)$/);
        items.push(`<li>${m ? m[1] : lines[i]}</li>`);
        i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }

    if (line.trim() === '') { out.push('<br/>'); i++; continue; }
    out.push(line + '<br/>');
    i++;
  }
  return out.join('').replace(/(<br\/>)+$/, '');
}
