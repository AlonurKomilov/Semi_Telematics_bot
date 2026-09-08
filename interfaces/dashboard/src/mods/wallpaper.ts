import type { PackMeta } from './packs/meta';
/**
 * The ground the app sits on.
 *
 * NOT AN IMAGE, and that is the design rather than a limitation. Three
 * things follow from it, and each is one this codebase had already
 * decided somewhere else:
 *
 *  1. `inject.ts` excludes `:` from every value it will write, which
 *     blocks every URL scheme — so `url()` cannot reach a token even by
 *     accident. Its own note says an image "needs its own reviewed path
 *     rather than a hole in this one". A pattern built from tokens needs
 *     no such path.
 *
 *  2. An arbitrary image cannot be PROVEN readable. The canvas gate
 *     refuses a background whose semantic tones would fall under
 *     AA-large; a photograph has a different answer per pixel and no
 *     honest way to gate it. A pattern derived from `--sidebar` and
 *     `--primary` has extremes that are already tokens, so the worst
 *     stop can be measured — and `wallpaper.test.ts` measures it.
 *
 *  3. Zero bytes. A curated set of photographs is the kind of thing
 *     that quietly adds a megabyte to a dashboard people open on a
 *     tethered phone in a yard.
 *
 * WHERE IT PAINTS: the chrome envelope — the sidebar, the header and
 * the gutters, which `AppShell` already calls "one continuous chrome
 * surface" and paints `bg-sidebar`. The content card sits IN that, so
 * the pattern is the ground around the work rather than under the
 * words. It is the same relationship GX has: the wallpaper is behind
 * the browser, and the page sits on top of it.
 *
 * A custom image is a later, separate decision — the way `brand`
 * followed `accent`. It needs storage, a tenancy path, a CSP change and
 * a readability answer, and none of those are this.
 *
 * SOME PATTERNS ARE `data:` SVG, and that is not a hole in the rule
 * above. What the rule refuses is a background nothing can reason
 * about; an inline `feTurbulence` is generated, tiles, weighs a few
 * hundred bytes and — the part that matters — renders GREYSCALE, so its
 * extremes are black and white at a stated opacity. The gate measures
 * both, which is the same guarantee a `color-mix` stop gives. A network
 * URL is still refused: that is a picture nobody here has seen.
 *
 * SOME OF THEM MOVE, and it is the same axis. Still and live paint the
 * same place — the ground under the chrome — so two axes would be two
 * claims on one set of pixels; whether it moves is a property of the
 * pack, the way it is in GX's wallpaper list, not a second decision a
 * person makes. "Live" stays a word for a background that keeps
 * running: a still texture is never called it.
 */
export type WallpaperKind = 'still' | 'live';

export interface Wallpaper extends PackMeta {
  readonly kind: WallpaperKind;
}

/**
 * What a live pattern's keyframes may animate — and nothing else.
 *
 * These are the compositor's properties: a layer that only transforms
 * is drawn once and moved by the GPU, so a live wallpaper costs a
 * texture and not a repaint per frame. They are also the properties the
 * contrast gate does not need to see change: moving a layer moves the
 * stops around, it does not create a stop. A keyframe that touched
 * `opacity`, a colour or a gradient would have an extreme the gate never
 * measured — so it is refused, by `wallpaper.test.ts`, not by taste.
 */
export const LIVE_ANIMATES = ['transform', 'translate', 'rotate', 'scale'] as const;

// The patterns themselves — the list and the CSS — live in
// `mods/packs/wallpaper/`. This file is the mechanism: what a wallpaper
// IS, where it paints, what a live one may move, and what it must not
// make unreadable.

/** The base every pattern tints. Named here so the guard measures the
 *  same ground the stylesheet paints. */
export const WALLPAPER_BASE = '--sidebar';
/** The text that sits on it — nav labels, the header. */
export const WALLPAPER_INK = '--sidebar-foreground';

/**
 * The floor a pattern's stops are held to, not a ceiling on how far
 * they may mix.
 *
 * The first version of this was a single percentage — "no stop may mix
 * more than 10%" — and the guard broke it on the first run: the Grid
 * pattern draws LINES at 55% of `--sidebar-border`, which is not a wash
 * over the whole surface and has nothing to do with how far a tint
 * shifts a ground. One number could not mean both.
 *
 * So there is no percentage rule. Every stop a pattern declares is
 * measured where it actually lands — the base mixed with THAT token at
 * THAT percentage — and the sidebar ink must clear AA over it. That is
 * the same shape as the canvas gate: not a rule about how colours are
 * written, a measurement of what they do.
 *
 * `wallpaper.test.ts` reads the stops out of `index.css` rather than
 * from a copy here, so a pattern that drifts fails on the file that
 * paints it.
 */
export const WALLPAPER_AA = 4.5;
