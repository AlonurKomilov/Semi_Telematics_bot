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
 * And none of them MOVES. "Live" is the word for a background that
 * keeps running, and nothing here does — calling a still texture live
 * would be a promise the thing does not keep, and it would spend the
 * name the moving one will need.
 */
export interface Wallpaper {
  /** Stored value, and what `data-wallpaper` is stamped with. */
  readonly id: string;
  /** What a person reads on the chip. */
  readonly label: string;
  /** One line, in the panel's own voice. */
  readonly why: string;
}

export const WALLPAPERS: readonly Wallpaper[] = [
  { id: 'none',  label: 'None',  why: 'Flat chrome, the way it has always been' },
  { id: 'mesh',  label: 'Mesh',  why: 'Two soft pools of the accent, low in the corners' },
  { id: 'grid',  label: 'Grid',  why: 'Fine ruled lines, like engineering paper' },
  { id: 'grain', label: 'Grain', why: 'A fine tooth, the way paper stock has one' },
  { id: 'paper', label: 'Paper', why: 'Drawn fibres, as if the chrome were pressed sheet' },
  { id: 'plasma', label: 'Plasma', why: 'Slow accent clouds under a fine tooth' },
];

export const WALLPAPER_IDS = WALLPAPERS.map((w) => w.id);

export const wallpaperById = (id: string): Wallpaper | undefined =>
  WALLPAPERS.find((w) => w.id === id);

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
