/**
 * The stylesheet as the browser sees it.
 *
 * `index.css` `@import`s one file per mod pack, and Vite inlines those
 * before the browser ever sees the sheet. A test that read `index.css`
 * alone would find the mechanism and none of the packs — every
 * wallpaper, cursor and shader guard would be measuring an empty set
 * and passing. This does what Vite does: replaces each local `@import`
 * with the file it names. Package imports (`@fontsource-…`) are left as
 * they are; nothing here measures them.
 */
import { readFileSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..');

export function assembledCss(): string {
  const entry = join(SRC, 'index.css');
  return readFileSync(entry, 'utf8').replace(
    /^@import\s+['"](\.[^'"]+)['"];\s*$/gm,
    (_m, rel: string) => readFileSync(resolve(dirname(entry), rel), 'utf8'),
  );
}

/** The mechanism alone — `index.css` with its pack imports NOT inlined. */
export function engineCss(): string {
  return readFileSync(join(SRC, 'index.css'), 'utf8');
}
