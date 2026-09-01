/**
 * Live theme tokens for the 3D canvas.
 *
 * A WebGL scene can't use CSS classes, and a colour baked into a
 * material is invisible to the theme picker — the same trap as
 * JS-drawn radii (see the SegmentTab precedent in the design rules).
 * So the canvas reads the computed tokens at mount and RE-READS them
 * whenever the <html> element's theme attributes change: a CSS-var
 * change alone never re-renders React.
 */
import { useEffect, useState } from 'react';

export interface SceneTokens {
  foreground: string;
  muted: string;
  mutedForeground: string;
  border: string;
  card: string;
  primary: string;
}

// The tokens are authored as oklch(...), which THREE.Color cannot
// parse (it silently keeps the old colour and warns — per frame, per
// node).  The browser CAN parse them: a 2D canvas context normalises
// any CSS colour to #rrggbb / rgba(...), and we reduce the rgba case
// to hex ourselves since three ignores (and warns about) alpha.
const ctx = document.createElement('canvas').getContext('2d');

function toHex(value: string, fallback: string): string {
  if (!ctx) return fallback;
  ctx.fillStyle = fallback;
  ctx.fillStyle = value;        // invalid input leaves the fallback
  const out = ctx.fillStyle;
  const m = /^rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(out);
  if (m) {
    const h = (n: string) => Number(n).toString(16).padStart(2, '0');
    return `#${h(m[1])}${h(m[2])}${h(m[3])}`;
  }
  return out;
}

function read(): SceneTokens {
  const s = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) =>
    toHex(s.getPropertyValue(name).trim() || fallback, fallback);
  return {
    foreground: v('--foreground', '#18181b'),
    muted: v('--muted', '#f4f4f5'),
    mutedForeground: v('--muted-foreground', '#71717a'),
    border: v('--border', '#e4e4e7'),
    card: v('--card', '#ffffff'),
    primary: v('--primary', '#2563eb'),
  };
}

export function useSceneTokens(): SceneTokens {
  const [tokens, setTokens] = useState<SceneTokens>(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTokens(read()));
    observer.observe(document.documentElement, {
      // `data-accent` as well as `data-theme`: attributeFilter is a
      // WHITELIST, so an attribute it does not name produces no callback
      // at all — nothing throws and nothing logs, the colours simply
      // stop following the picker.
      attributes: true,
      // `data-mod` because an injected palette arrives as a <style>
      // element, which fires no attribute mutation of its own — see
      // mods/inject.ts. Without it this scene keeps the boot theme's
      // six colours for the life of the page.
      attributeFilter: ['class', 'data-theme', 'data-accent', 'data-mod'],
    });
    return () => observer.disconnect();
  }, []);
  return tokens;
}
