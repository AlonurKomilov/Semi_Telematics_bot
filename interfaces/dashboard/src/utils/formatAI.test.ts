import { describe, it, expect } from 'vitest';
import { formatAIResponse } from './formatAI';

// The AI answer is rendered via dangerouslySetInnerHTML, and user-controlled
// fleet data (company / vehicle / driver names) flows into it — so this is an
// XSS boundary.  These cases include the exact payloads the previous regex
// denylist could slip past.  A payload is safe if it produces no LIVE markup
// when re-parsed: either DOMPurify stripped it, or the markdown path escaped
// it into inert text (`&lt;img…` renders as visible text, not an element).
// `hasLiveXSS` checks the DOM, so it correctly accepts both outcomes and
// rejects only an actually-executable element/attribute.
function hasLiveXSS(html: string): boolean {
  const div = document.createElement('div');
  div.innerHTML = html;
  if (div.querySelector('script, iframe, object, embed, svg, math')) return true;
  for (const el of Array.from(div.querySelectorAll('*'))) {
    for (const attr of Array.from(el.attributes)) {
      if (/^on/i.test(attr.name)) return true;
      if (/^(href|src|xlink:href)$/i.test(attr.name) && /^\s*javascript:/i.test(attr.value)) {
        return true;
      }
    }
  }
  return false;
}

describe('formatAIResponse — sanitisation (XSS boundary)', () => {
  it('neutralises <script> tags', () => {
    expect(hasLiveXSS(formatAIResponse('<p>hi</p><script>alert(1)</script>'))).toBe(false);
  });

  it('neutralises UNQUOTED event handlers (regex-denylist bypass)', () => {
    // The old sanitizer only matched on*="..." / on*=\'...\' with quotes.
    expect(hasLiveXSS(formatAIResponse('<p onload=alert(1)>boom</p>'))).toBe(false);
  });

  it('neutralises <svg onload> vectors (not covered by the old denylist)', () => {
    expect(hasLiveXSS(formatAIResponse('<svg/onload=alert(1)>'))).toBe(false);
  });

  it('neutralises <img onerror> injected via a fleet name', () => {
    // e.g. a vehicle named: <img src=x onerror=alert(document.cookie)>
    expect(hasLiveXSS(formatAIResponse('Vehicle <img src=x onerror=alert(1)> is offline'))).toBe(false);
  });

  it('neutralises javascript: hrefs', () => {
    expect(hasLiveXSS(formatAIResponse('<a href="javascript:alert(1)">click</a>'))).toBe(false);
  });

  it('neutralises <iframe>/<object> embeds', () => {
    expect(hasLiveXSS(formatAIResponse('<iframe src="evil"></iframe><object data="x"></object>'))).toBe(false);
  });

  it('a clean formatted answer is (of course) not flagged', () => {
    expect(hasLiveXSS(formatAIResponse('**Truck 116** is *offline*.'))).toBe(false);
  });
});

describe('formatAIResponse — legitimate formatting preserved', () => {
  it('keeps bold/italic markdown', () => {
    const out = formatAIResponse('This is **bold** and *italic*.');
    expect(out).toContain('<strong>bold</strong>');
    expect(out).toContain('<em>italic</em>');
  });

  it('keeps bullet lists', () => {
    const out = formatAIResponse('- Truck 116\n- Truck 226');
    expect(out).toContain('<li>Truck 116</li>');
    expect(out).toContain('<ul');
  });

  it('keeps tables with their styling classes', () => {
    const html =
      '<div class="overflow-x-auto"><table class="w-full">' +
      '<thead><tr><th class="px-2">Truck</th></tr></thead>' +
      '<tbody><tr><td class="px-2">116</td></tr></tbody></table></div>';
    const out = formatAIResponse(html);
    expect(out).toContain('<table');
    expect(out).toContain('<th');
    expect(out).toContain('116');
    expect(out).toContain('class='); // styling classes survive sanitisation
  });

  it('escapes stray angle brackets in plain text', () => {
    const out = formatAIResponse('5 < 10 and 10 > 5');
    expect(out).not.toContain('<10');
  });
});
