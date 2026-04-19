/**
 * Sanitize and format AI response text for safe HTML rendering.
 * Escapes raw HTML, then re-enables safe formatting tags.
 */
export function formatAIResponse(text: string): string {
  let s = text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
  // Bold: **text** or <b>text</b>
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/&lt;b&gt;(.+?)&lt;\/b&gt;/g, '<strong>$1</strong>');
  // Italic: *text* or <i>text</i>
  s = s.replace(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/g, '<em>$1</em>');
  s = s.replace(/&lt;i&gt;(.+?)&lt;\/i&gt;/g, '<em>$1</em>');
  // Bullet lists: lines starting with - or •
  s = s.replace(/^[•\-]\s+(.+)$/gm, '<li>$1</li>');
  s = s.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul class="list-disc ml-4 my-1">$1</ul>');
  // Line breaks
  s = s.replace(/\n/g, '<br/>');
  return s;
}
