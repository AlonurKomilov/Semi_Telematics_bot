/**
 * The faces this app ships — one `.css` file each, listed here. Geist
 * has no file: it IS the base `--font-sans`.
 *
 * Every stack costs zero bytes — four faces the operating system
 * already ships. A bundled webfont later is one `@import` at the top of
 * `index.css`, one pack file, and one row here.
 */
import type { FontPack } from '../../catalogue';

export const FONT_PACKS: readonly FontPack[] = [
  { id: 'geist',   label: 'Geist',   note: 'The one this app was drawn with' },
  { id: 'system',  label: 'System',  note: 'Whatever your computer uses' },
  { id: 'serif',   label: 'Serif',   note: 'Book-like, with strokes on the letters' },
  { id: 'mono',    label: 'Mono',    note: 'Every letter the same width' },
  { id: 'rounded', label: 'Rounded', note: 'Softer corners on the letters' },
];
export const MOD_FONTS = FONT_PACKS.map((f) => f.id);
