/**
 * Sanitize and format AI response text for safe HTML rendering.
 * Handles: bold, italic, headings, bullet lists, numbered lists, tables, line breaks.
 * Processes line-by-line so block elements are never split by stray <br/> tags.
 *
 * Two paths:
 *  1. Content already contains HTML tags (backend returned HTML) →
 *     strip only dangerous tags/attributes, pass safe markup through.
 *  2. Plain markdown/text → escape then convert markdown to HTML.
 */

const RE_DANGEROUS_TAGS = /<\/?(script|style|iframe|object|embed|form|input|textarea|select|button|link|meta|base)\b[^>]*>/gi;
const RE_EVENT_ATTRS = /\s+on\w+="[^"]*"/gi;
const RE_EVENT_ATTRS2 = /\s+on\w+='[^']*'/gi;
const RE_JS_HREF = /href\s*=\s*["']javascript:[^"']*["']/gi;

function looksLikeHTML(text: string): boolean {
  return /<(p|ul|ol|li|h[1-6]|div|br|strong|em|table|thead|tbody|tr|td|th)\b[^>]*>/i.test(text);
}

function sanitizeHTML(html: string): string {
  return html
    .replace(RE_DANGEROUS_TAGS, '')
    .replace(RE_EVENT_ATTRS, '')
    .replace(RE_EVENT_ATTRS2, '')
    .replace(RE_JS_HREF, '');
}

export function formatAIResponse(text: string): string {
  // If the backend already returned HTML, sanitize dangerous parts and return
  if (looksLikeHTML(text)) {
    return sanitizeHTML(text);
  }

  // Step 1: HTML-escape raw input
  let s = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  // Step 2: Inline formatting
  // Bold: **text** or <b>text</b> or <strong>text</strong>
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/&lt;b&gt;(.+?)&lt;\/b&gt;/g, '<strong>$1</strong>');
  s = s.replace(/&lt;strong&gt;(.+?)&lt;\/strong&gt;/g, '<strong>$1</strong>');
  // Italic: *text* or <i>text</i>
  s = s.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
  s = s.replace(/&lt;i&gt;(.+?)&lt;\/i&gt;/g, '<em>$1</em>');

  // Step 3: Block-level processing (line by line)
  const lines = s.split('\n');
  const out: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Headings: ### or ##
    const h3 = line.match(/^###\s+(.+)$/);
    if (h3) {
      out.push(`<h3 class="font-semibold text-sm mt-3 mb-1">${h3[1]}</h3>`);
      i++; continue;
    }
    const h2 = line.match(/^##\s+(.+)$/);
    if (h2) {
      out.push(`<h2 class="font-bold text-sm mt-3 mb-1">${h2[1]}</h2>`);
      i++; continue;
    }

    // Tables: collect consecutive | ... | lines
    if (/^\|.+\|/.test(line)) {
      const tableLines: string[] = [];
      while (i < lines.length && /^\|.+\|/.test(lines[i])) {
        tableLines.push(lines[i]); i++;
      }
      const dataRows = tableLines.filter(r => !/^\|[-:| ]+\|$/.test(r));
      if (dataRows.length >= 2) {
        const [hdr, ...body] = dataRows;
        const ths = hdr.split('|').filter(Boolean)
          .map(c => `<th class="px-2 py-1 text-left text-xs font-medium">${c.trim()}</th>`)
          .join('');
        const trs = body.map(r =>
          `<tr class="border-t border-border/40">${r.split('|').filter(Boolean)
            .map(c => `<td class="px-2 py-1 text-xs">${c.trim()}</td>`)
            .join('')}</tr>`
        ).join('');
        out.push(
          `<div class="overflow-x-auto my-2"><table class="w-full border-collapse">` +
          `<thead class="bg-muted/60"><tr>${ths}</tr></thead>` +
          `<tbody>${trs}</tbody></table></div>`
        );
      } else {
        tableLines.forEach(l => out.push(l + '<br/>'));
      }
      continue;
    }

    // Numbered lists: collect consecutive `N. item` lines
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        const m = lines[i].match(/^\d+\.\s+(.+)$/);
        items.push(`<li>${m ? m[1] : lines[i]}</li>`);
        i++;
      }
      out.push(`<ol class="list-decimal ml-4 my-1 space-y-0.5">${items.join('')}</ol>`);
      continue;
    }

    // Bullet lists: collect consecutive `• item` or `- item` lines
    if (/^[•\-]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[•\-]\s+/.test(lines[i])) {
        const m = lines[i].match(/^[•\-]\s+(.+)$/);
        items.push(`<li>${m ? m[1] : lines[i]}</li>`);
        i++;
      }
      out.push(`<ul class="list-disc ml-4 my-1 space-y-0.5">${items.join('')}</ul>`);
      continue;
    }

    // Empty line → spacing
    if (line.trim() === '') {
      out.push('<br/>');
      i++; continue;
    }

    // Regular line
    out.push(line + '<br/>');
    i++;
  }

  // Remove trailing line break
  return out.join('').replace(/(<br\/>)+$/, '');
}
