import { expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
const directory = resolve(dirname(fileURLToPath(import.meta.url)), '../../locales');
it('ships the same Chat vocabulary and interpolation fields in all nine locales', () => {
  const files = readdirSync(directory).filter(f => f.endsWith('.json'));
  const english = JSON.parse(readFileSync(`${directory}/en.json`, 'utf8')).chat as Record<string, string>;
  expect(files).toHaveLength(9);
  for (const file of files) {
    const words = JSON.parse(readFileSync(`${directory}/${file}`, 'utf8')).chat as Record<string, string>;
    expect(Object.keys(words).sort(), file).toEqual(Object.keys(english).sort());
    for (const [key, value] of Object.entries(words)) {
      expect(value.trim().length, `${file}:${key}`).toBeGreaterThan(0);
      expect(value.match(/{{\w+}}/g) ?? [], `${file}:${key}`).toEqual(english[key].match(/{{\w+}}/g) ?? []);
    }
  }
});
