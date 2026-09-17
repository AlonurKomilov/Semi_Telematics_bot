/**
 * The material layer is three claims about the CASCADE, and a claim
 * about cost. None of them are visible by reading a component, so they
 * are asserted against the stylesheet itself.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { assembledCss, engineCss } from '../../test/stylesheet';

const SRC = join(__dirname, '..', '..');
// Assembled: glass is a pack file now, inlined the way Vite inlines it.
const CSS = assembledCss();
/** Comments blanked in place, so line numbers survive. */
const CODE = CSS.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' '));

describe('where the material rules sit in the cascade', () => {
  it('defines .surface outside every @layer', () => {
    // Tailwind v3 orders its output rather than emitting native cascade
    // layers, so a rule written after `@tailwind utilities` in source
    // wins a specificity tie against a utility. Inside `@layer
    // components` it would be hoisted ABOVE the utilities and `bg-card`
    // would beat it — the surface would silently stay opaque.
    const idx = CODE.indexOf('\n.surface {');
    expect(idx, '.surface is not declared at top level').toBeGreaterThan(0);
    const before = CODE.slice(0, idx);
    // Every @layer opened before this point must also have closed.
    const opens = (before.match(/@layer\s+\w+\s*\{/g) || []).length;
    const braces = (before.match(/\{/g) || []).length - (before.match(/\}/g) || []).length;
    expect(opens, 'no @layer blocks at all — has the file changed shape?').toBeGreaterThan(0);
    expect(braces, '.surface is inside an unclosed block, probably an @layer').toBe(0);
  });

  it('keeps every glass rule inside @media screen', () => {
    // The print reset restores the light palette through selector
    // specificity alone — there is no `!important` anywhere in this
    // codebase. A glass rule that applied during print would blur and
    // thin a page about to become paper.
    const glass = [...CODE.matchAll(/\[data-material="glass"\]/g)].map((m) => m.index!);
    expect(glass.length, 'no glass rules found').toBeGreaterThan(1);
    for (const at of glass) {
      const before = CODE.slice(0, at);
      const screens = (before.match(/@media\s+screen\s*\{/g) || []).length;
      const opened = (before.match(/\{/g) || []).length - (before.match(/\}/g) || []).length;
      // The escape hatch names the attribute in a selector that must
      // work everywhere, including print. It is the one exception.
      const line = CODE.slice(at, CODE.indexOf('\n', at) + 200);
      if (line.includes('surface-opaque')) continue;
      expect(screens, `a glass rule at offset ${at} is outside @media screen`).toBeGreaterThan(0);
      expect(opened, `a glass rule at offset ${at} is not nested in a block`).toBeGreaterThan(0);
    }
  });
});

describe('what the solid path costs', () => {
  const surfaceRule = /\n\.surface \{([^}]*)\}/.exec(CODE)?.[1] ?? '';

  it('puts no backdrop-filter on the base class', () => {
    // At blur 0 it would still promote all 178 cards to their own
    // compositing layer for no visual effect. The default has to cost
    // exactly nothing.
    expect(surfaceRule, '.surface rule not found').not.toBe('');
    expect(surfaceRule).not.toMatch(/backdrop-filter/);
  });

  it('puts no box-shadow on the base class either', () => {
    // This rule is unlayered and so outranks a utility. A shadow here
    // would beat the `shadow-lg` that dialogs, sheets and menus already
    // carry and silently flatten every one of them.
    expect(surfaceRule).not.toMatch(/box-shadow/);
    // It belongs to the glass rule, which is where it is measured from.
    // Matched by extracting that rule rather than by a distance window —
    // a window silently stops matching when someone adds a comment.
    const glassSurface = /:root\[data-material="glass"\]\s+\.surface\s*\{([\s\S]*?)\n {2}\}/.exec(CODE)?.[1];
    expect(glassSurface, 'the glass .surface rule is gone').toBeTruthy();
    // `var(--surface-shadow)` is the DROP shadow and is now the last
    // entry in a list that starts with the glass edge — an inset
    // highlight and an inset shade, which is what gives a pane
    // thickness. Matched as "present", not as "the whole value", so
    // adding a layer to the edge is not a test failure while losing the
    // drop shadow still is.
    expect(glassSurface!).toMatch(/var\(--surface-shadow\)/);
    expect(glassSurface!).toMatch(/backdrop-filter/);
  });

  /**
   * GLASS HAS AN EDGE AND A BODY, not just a fill and a blur.
   *
   * The first version of the pack was a translucent rectangle: no lit
   * edge, no brighter top, nothing that says the surface has thickness.
   * That is frosted plastic, and it is what the owner saw when he
   * compared it to a material that refracts.
   *
   * Both halves ride `--glass-sheen`, one knob, so they can never drift
   * apart — and both are painted through `box-shadow: inset` and
   * `background-image`, NEVER a pseudo-element: 21 `.surface` elements
   * in this product are not positioned, so a `::before` at `inset: 0`
   * would anchor to whatever is positioned further up. That is exactly
   * the mistake the wallpaper packs made, and a pack may not require an
   * element to be positioned any more than it may position one.
   */
  it('gives a pane a lit edge and a body that catches light', () => {
    const glassSurface = /:root\[data-material="glass"\]\s+\.surface\s*\{([\s\S]*?)\n {2}\}/
      .exec(CODE)?.[1] ?? '';
    expect(glassSurface, 'the glass .surface rule is gone').not.toBe('');
    // TWO insets, and the count is the claim: an edge is a lit TOP and
    // a shaded FOOT, and one without the other is a line rather than a
    // thickness. A single `inset` passed while the highlight had been
    // deleted — a mutation found that.
    const shadow = /box-shadow:([\s\S]*?);/.exec(glassSurface)?.[1] ?? '';
    expect(
      (shadow.match(/\binset\b/g) ?? []).length,
      'glass lost half its edge. A lit top without a shaded foot is a line, not a '
        + 'thickness — and a translucent rectangle with neither is frosted plastic.',
    ).toBe(2);
    expect(glassSurface, 'glass lost its sheen — the body no longer catches light')
      .toMatch(/background-image:[\s\S]*linear-gradient/);
    // Told, not sampled. CSS cannot look at what is behind a surface;
    // `--ground` is what it is TOLD, so a pane over the rail and one
    // over the page are different glass without either knowing how.
    expect(glassSurface, 'the ground tint is gone — every pane is the same glass again')
      .toMatch(/var\(--ground/);
  });

  /**
   * THE RIM IS TWO COLOURS, and this is the test my own first version
   * would have failed.
   *
   * It used white for the top edge in both modes, and in light that is
   * invisible — not faint, INVISIBLE: `--card`, `--background` and
   * `--popover` are all `oklch(1 0 0)` there, so a white highlight sits
   * on white. No amount of tuning the number fixes a colour that is the
   * same as the thing under it.
   *
   * What gives a white pane thickness on a white ground is a DARKER
   * foot, because there is nothing brighter than white to put on top.
   * Dark is the opposite case: the pane is lighter than the ground, so
   * the top edge is the only thing separating it from the pane behind.
   */
  it('gives light and dark different rims, because they are not the same problem', () => {
    const rimOf = (sel: string) => {
      const block = new RegExp(sel + '\\[data-material="glass"\\]\\s*\\{([\\s\\S]*?)\\n {2}\\}')
        .exec(CODE)?.[1] ?? '';
      return {
        top: (/--glass-rim-top:([^;]*);/.exec(block)?.[1] ?? '').trim(),
        foot: (/--glass-rim-foot:([^;]*);/.exec(block)?.[1] ?? '').trim(),
      };
    };
    const light = rimOf(':root');
    const dark = rimOf('\\.dark');
    expect(light.top, 'the light rim is gone').not.toBe('');
    expect(dark.top, 'the dark rim is gone').not.toBe('');
    expect(
      light.top,
      'both modes declare the same top edge. In light every surface token is '
        + '`oklch(1 0 0)`, so a white highlight is invisible there — the two modes '
        + 'are not one problem at two strengths.',
    ).not.toBe(dark.top);
  });

  it('and no pseudo-element, because 21 surfaces are not positioned', () => {
    expect(
      CODE,
      'a material pack grew a `::before`. 21 `.surface` elements are unpositioned, '
        + 'so one at `inset: 0` anchors to whatever is positioned further up — the '
        + 'wallpaper packs made this exact mistake with `position: relative`.',
    ).not.toMatch(/\[data-material="glass"\][^{]*::(before|after)/);
  });

  it('and holds no VALUE of its own — only the neutral of each kind', () => {
    // THE LINE THE OWNER ASKED ABOUT, made mechanical. The list above
    // pins exact strings, which is a copy of today rather than a rule:
    // change a default to 0.2, update the literal, and it still passes
    // while the engine has quietly acquired a material's taste.
    //
    // This asks the other question — is the value the one that makes
    // the material DO NOTHING? A strength of 0, a multiplier of 1, a
    // length of 0, a colour of transparent, a shadow of none. Anything
    // else is somebody's choice, and a choice belongs to a pack: the
    // engine is what a pack is read INTO, so a number sitting here is a
    // number no pack can be blamed for and no reader can find.
    const NEUTRAL = new Set(['0', '0px', '1', 'none', 'transparent']);
    const ENGINE = engineCss().replace(/\/\*[\s\S]*?\*\//g, '');
    const found: string[] = [];
    for (const m of ENGINE.matchAll(/(--surface-[a-z-]+):\s*([^;]+);/g)) {
      // `--surface-base` is not a material value: it says which plane of
      // the PALETTE a surface sits on, which is the app's own fact and
      // the thing a material then decides what to do with.
      if (m[1] === '--surface-base') continue;
      found.push(m[1]);
      expect(NEUTRAL.has(m[2].trim()),
        `${m[1]} is "${m[2].trim()}" in the engine sheet. That is a material's choice, `
        + 'and it belongs in a pack — everything here has to be the value that does nothing.')
        .toBe(true);
    }
    expect(found.length, 'no surface token was read — this measures nothing')
      .toBeGreaterThan(5);
  });

  it('ships solid defaults that reproduce today exactly', () => {
    // The ENGINE sheet, by name: the first declaration of each token is
    // the base, and in the assembled sheet the glass pack — imported at
    // the top — declares them first. A default read from there would be
    // glass's, and this test would be asserting the wrong material.
    const ENGINE = engineCss().replace(/\/\*[\s\S]*?\*\//g, '');
    // THE WASH'S NEUTRAL CHANGED WITH THE CONCEPT, not to make a test
    // pass. This read `--surface-alpha: 1` — "the surface paints all of
    // its own colour" — back when one number meant both how much and of
    // what. Split into a colour and a strength, solid's answer is that
    // it lays NO wash at all: it paints its own colour opaquely through
    // the base rung, which never reads either token. Same pixels, a
    // statement that can now be read.
    for (const [name, want] of [
      ['--surface-wash', 'transparent'], ['--surface-wash-page', '0'],
      ['--surface-wash-floating', '0'],
      ['--surface-blur-page', '0px'], ['--surface-blur-floating', '0px'],
      ['--surface-saturate', '1'], ['--surface-shadow', 'none'],
    ] as const) {
      const m = new RegExp(`${name}:\\s*([^;]+);`).exec(ENGINE);
      expect(m, `${name} is not declared`).not.toBeNull();
      expect(m![1].trim(), `${name} default`).toBe(want);
    }
  });
});

describe('the occlusion escape hatch', () => {
  it('exists, and cancels both the translucency and the blur', () => {
    // A surface positioned out of flow uses its fill to HIDE what it
    // covers, and glass makes it see-through instead. This comment used
    // to name "21 sticky and pinned elements" that opted out; none did,
    // and the grid's frozen columns were never at risk — they paint
    // opaque utilities of their own. The population this rule serves is
    // claimed by geometry now; `.surface-opaque` is for the one case
    // geometry cannot see.
    // Scoped to glass, and asserted that way. The rule was once a
    // two-selector list whose bare half did nothing — solid mode has no
    // translucency to cancel — and a mutation deleting that half was
    // invisible to every assertion here. A rule a mutation can remove
    // without breaking anything should not be in the file.
    expect(CODE, 'the escape hatch is no longer scoped to glass')
      .toMatch(/:root\[data-material="glass"\]\s+\.surface-opaque\s*[,{]/);
    const rule = /:root\[data-material="glass"\]\s+\.surface-opaque\s*(?:,[^{]*)?\{([^}]*)\}/
      .exec(CODE)?.[1] ?? '';
    expect(rule, '.surface-opaque is gone — the pinned columns have no way out').not.toBe('');
    expect(rule).toMatch(/background-color:\s*var\(--surface-base/);
    // Both spellings, separately. `/backdrop-filter/` matches inside
    // `-webkit-backdrop-filter`, so a mutation deleting the standard
    // property passed while only the prefixed one survived — which is
    // every modern browser ignoring the reset.
    expect(rule, 'the unprefixed reset is gone').toMatch(/(^|[\s;])backdrop-filter:\s*none/);
    expect(rule, 'the -webkit- reset is gone').toMatch(/-webkit-backdrop-filter:\s*none/);
  });

  /**
   * A POPOVER TAKES IT WITHOUT ASKING.
   *
   * The comment above says "those sites opt out explicitly". Not one
   * site did: `surface-opaque` existed in the glass sheet, in a comment
   * in `index.css`, and in the assertion above. Nowhere else. So every
   * floating surface in the product was translucent, and the persona
   * menu in the top bar rendered the SIDEBAR through itself — at alpha
   * 0.72 the text behind keeps 28% of its contrast, which is legible
   * grey whether or not the blur lands.
   *
   * Occlusion is not a property a call site should have to remember,
   * and this one proves the point: twenty-four sites carry
   * `.surface-popover`, and not one of them would have thought to add a
   * second class. What defines that category IS floating over content
   * nobody chose.
   */
  /**
   * The rule tests OCCLUSION, not the mechanism that achieves it —
   * which is the difference between a guard and a copy of the code.
   * There are two mechanisms now: drop the filter and paint a solid
   * fill, or keep the filter and flatten the backdrop with `contrast`
   * and `brightness` so no detail and no luminance get through. The
   * second is what the floating set uses, because dropping the filter
   * left glass cards beside slab menus — two materials in one
   * interface, which is a conflict a reader sees even where no test
   * could.
   */
  /**
   * IT READS THE NUMBERS, not the words.
   *
   * This used to ask whether the RULE mentioned `contrast` and
   * `brightness`, and that is not the same question. The mechanism and
   * the occlusion were separated on purpose — one chain for every
   * surface, the flattening carried entirely by three tokens — and the
   * first time those tokens were set to their identities the old check
   * sailed straight through, green, on exactly the state it exists to
   * forbid. A guard that measures the presence of a lever rather than
   * where the lever is set is not measuring anything.
   */
  const occlusion = (sel: string) => {
    const rules = [...CODE.matchAll(/([^{}]*)\{([^{}]*)\}/g)]
      .filter(([, s]) => new RegExp(`\\.surface\\${sel}(?![\\w-])`).test(s));
    if (rules.some(([, , body]) => /backdrop-filter:\s*none/.test(body))) return 'solid';
    const num = (block: string, tok: string) =>
      Number(new RegExp(`${tok}:\\s*([\\d.]+)`).exec(block)?.[1]);
    let light = '';
    for (const m of CODE.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1].trim().replace(/\s+/g, ' ') === ':root[data-material="glass"]') light += m[2];
    const contrast = num(light, '--surface-flatten-contrast');
    const alpha = num(light, '--surface-wash-floating');
    // Detail survives above roughly half contrast; below it the words
    // behind a menu are gone whatever the ground. A high fill occludes
    // on its own, filter or no filter.
    return (contrast <= 0.5 || alpha >= 0.6) ? 'flattened' : 'clear';
  };

  /**
   * A MENU SCATTERS WHAT IT COVERS. IT DOES NOT HIDE IT.
   *
   * The owner settled this after looking at three proposals and
   * rejecting two. A floating surface gets 4px of blur — the number
   * measured off the command palette's own overlay, which is the
   * frosting he recognised as frosting — and no wash, no flattening.
   * Blur scatters without painting, so a menu takes the tone of
   * whatever is under it and adds nothing of its own. That is the look
   * he was asking for across four separate attempts to describe it.
   *
   * IT IS NOT OCCLUSION AND THIS SAYS SO. Blur moves the words behind
   * a menu around; it does not remove them, and at 4px they survive as
   * grey shapes that compete with the menu's own. The persona menu
   * once rendered the sidebar through itself at alpha 0.72, where the
   * text behind keeps 28% of its contrast — this is milder than that
   * and the same kind of thing.
   *
   * So the floor is waived, explicitly, by one line. Putting it back
   * is deleting that line; the values that go with it are on the
   * tokens in `glass.css`, under their own names now.
   */
  const OWNER_CHOSE_BLUR_ALONE = true;

  it('and a popover takes it without having to ask', () => {
    const state = occlusion('.surface-popover');
    if (OWNER_CHOSE_BLUR_ALONE) {
      // The exemption is itself guarded: it may excuse the state the
      // owner asked for and nothing else, so a DIFFERENT failure still
      // fails rather than hiding behind his decision.
      expect(state, 'the waiver is covering something the owner did not choose')
        .toBe('clear');
      return;
    }
    expect(
      state,
      'a popover passes its backdrop through again. Every menu, select, context '
        + 'menu, dialog, sheet and banner floats over content nobody chose, so it '
        + 'must occlude — and no call site should have to remember that.',
    ).not.toBe('clear');
  });

  it('and each region reads its OWN blur, never the other\'s', () => {
    // This used to say a floating surface may not blur at all, because
    // the whole material was at 0 and a menu reaching for blur would be
    // the one surface made of something else. The owner settled it the
    // other way: a menu scatters what it covers by 4px — the number
    // measured off the command palette — and a card still does not,
    // because a card covers only the page's own ground.
    //
    // So the rule is no longer "no blur"; it is that the two regions
    // are SEPARATE. A page surface reading the floating blur would
    // frost all 178 cards at once, and a floating surface reading the
    // page's would silently go clear the day a card's blur changed.
    // Neither failure looks like a typo at the call site.
    const floating = /:root\[data-material="glass"\][^{}]*\.surface\.surface-popover[^{}]*\{([^{}]*)\}/
      .exec(CODE)?.[1] ?? '';
    const page = /:root\[data-material="glass"\]\s*\.surface\s*\{([^{}]*)\}/.exec(CODE)?.[1] ?? '';
    expect(floating, 'the floating rule is gone').not.toBe('');
    expect(page, 'the page rule is gone').not.toBe('');

    expect(floating, 'a floating surface blurs by the page\'s number')
      .not.toMatch(/--surface-blur-page/);
    expect(floating, 'a floating surface no longer scatters at all')
      .toMatch(/blur\(var\(--surface-blur-floating\)\)/);
    expect(page, 'a page surface blurs by the floating number — that is every card at once')
      .not.toMatch(/--surface-blur-floating/);
    expect(page, 'a page surface no longer reads a blur')
      .toMatch(/blur\(var\(--surface-blur-page\)\)/);
  });

  /**
   * AND THE CATEGORY IS GEOMETRY, not a class somebody remembered.
   *
   * `.surface-popover` was the first answer and it did not reach the
   * reported bug at all: the persona menu is a `<Card>`, and
   * `cardVariants` is `"surface border border-border rounded-lg"` — no
   * popover class, and no call site would have thought to add one.
   *
   * What these surfaces share is not a name. They are positioned out of
   * flow, over content nobody chose, and the product writes that in
   * bare Tailwind tokens only — so three selectors claim thirteen sites
   * with no call-site edits and nothing left to forget.
   */
  it('and a surface positioned out of flow occludes, whatever it is called', () => {
    for (const pos of ['absolute', 'fixed', 'sticky']) {
      const state = occlusion(`.${pos}`);
      // Same exemption, same reason, and the same refusal to cover
      // anything else: these three selectors and `.surface-popover` are
      // one rule, so they can only ever be in one state together.
      if (OWNER_CHOSE_BLUR_ALONE) {
        expect(state, `\`.surface.${pos}\` is not in the state the owner asked for`)
          .toBe('clear');
        continue;
      }
      expect(
        state,
        `a \`.surface.${pos}\` passes its backdrop through again. It floats over content nobody `
          + 'chose, so it must occlude — and the class that says so is the one the '
          + 'author already wrote for layout.',
      ).not.toBe('clear');
    }
  });

  /**
   * AND THE FRAME IS NOT GLASS.
   *
   * `.chrome-pane` is by definition the surface that steps aside for a
   * wallpaper: with a pattern on it is transparent, so there is nothing
   * of its own to frost, and with no pattern it is an opaque
   * full-height rail, so the blur has nothing behind it to show.
   *
   * It paid for both in the only currency that matters here.
   * `backdrop-filter` creates a stacking context and a containing block
   * for positioned descendants, and the sidebar, the header and the
   * content envelope are the three ancestors every menu in this product
   * opens inside — the persona list, the avatar menu, the mods panel.
   * All three went wrong under Glass and were correct under Solid, and
   * this is the only property that differs between them there.
   */
  it('and the frame carries the material\'s uniform half and nothing else', () => {
    // THIS RULE USED TO SAY THE OPPOSITE, and the reason it did has
    // been removed rather than overruled. A `backdrop-filter` makes an
    // element a backdrop root, so a menu opening inside one of these
    // panes would stop occluding — and three did open inside them: the
    // persona list, the account menu and the mods picker. All three are
    // portalled now, and `shells/frame.test.ts` is what keeps them
    // there. Without that guard this rule is the bug it was forbidden
    // for, which is why the two are worth reading together.
    //
    // What the frame may take is the half of the material that does the
    // same thing to every pixel it covers. The lens does not, and the
    // numbers say so: the pack's band is 30px and three of the five
    // sides are 8px gutters. A side thinner than the band is displaced
    // end to end — distortion, not a rim — and bending only its inner
    // edge changes nothing about that while costing a declaration per
    // side, which is exactly what unifying the frame was meant to stop
    // needing.
    const rule = /:root\[data-material="glass"\]\s*\.surface\.chrome-pane\s*\{([^{}]*)\}/
      .exec(CODE)?.[1] ?? '';
    expect(rule, 'the frame has no rule of its own any more').not.toBe('');

    const filter = /(?:^|[^-])backdrop-filter:\s*([^;]+);/.exec(rule)?.[1] ?? '';
    expect(filter, 'the frame takes nothing from the material').toContain('saturate(');
    // The two that are not uniform. A lens bends at an edge, and every
    // edge here but one is a seam with another side of the same frame;
    // a blur would soften the frame's own pattern, which sits on the
    // plane behind these panes rather than through them.
    expect(filter, 'the frame was given the lens — see the 30px band against an 8px gutter')
      .not.toMatch(/--surface-lens/);
    expect(filter, 'the frame was given a blur, which softens its own wallpaper')
      .not.toMatch(/blur\(/);
  });

  it('and the call-site hatch still cancels the filter outright', () => {
    // `.surface-opaque` is the one a call site takes when it KNOWS
    // geometry cannot see its case. It shared a rule with the frame
    // until the frame needed a filter; splitting them is what let the
    // frame have one, so this checks the half that did not move.
    const opaque = /:root\[data-material="glass"\]\s*\.surface-opaque\s*\{([^{}]*)\}/
      .exec(CODE)?.[1] ?? '';
    expect(opaque, 'the opaque hatch is gone').not.toBe('');
    expect(opaque, 'the hatch stopped cancelling the filter').toMatch(/backdrop-filter:\s*none/);
  });

  /**
   * `.surface.surface-popover`, never `.surface-popover` alone.
   *
   * The glass rung is `:root[data-material="glass"] .surface` at
   * (0,2,0). A single-class override ties it, and a tie is decided by
   * which line came last — a rule this sheet already learned about
   * vendor stylesheets, where the answer differed between `vite dev`
   * and the production build.
   */
  it('and it out-specifies the rung it overrides rather than tying it', () => {
    expect(CODE, 'the popover override ties the glass rung instead of beating it')
      .not.toMatch(/:root\[data-material="glass"\]\s+\.surface-popover\s*[,{]/);
  });
});

describe('the primitives actually use it', () => {
  // Matched against the CLASS STRING, not the word. `card.tsx` says
  // "the bordered surface a page's content sits on" in its own doc
  // comment, so a search for `surface` passed even after the primitive
  // was reverted to `bg-card` — the guard was reading prose.
  const FILES = [
    ['components/ui/card.tsx', 'cva("surface '],
    ['components/ui/dialog.tsx', 'surface surface-popover'],
    ['components/ui/sheet.tsx', 'surface surface-popover'],
    ['components/ui/select.tsx', 'surface surface-popover'],
    ['components/ui/context-menu.tsx', 'surface surface-popover'],
  ] as const;

  it('reaches every centralised surface', () => {
    // These five strings are the entire reason a material is cheap:
    // they define ~387 call sites between them. A primitive that drifts
    // back to a raw `bg-card` takes its whole subtree out of the
    // material without any test noticing otherwise.
    for (const [file, needle] of FILES) {
      const src = readFileSync(join(SRC, file), 'utf8');
      expect(src, `${file} no longer uses "${needle}"`).toContain(needle);
    }
  });

  it('leaves the inverted tooltip alone', () => {
    // Tooltip paints `bg-foreground` — it is not a surface, it is the
    // ink. Pulling it into the material would make it glass over
    // whatever it points at, which is the one place that must stay
    // readable.
    const src = readFileSync(join(SRC, 'components/ui/tooltip.tsx'), 'utf8');
    expect(src).toContain('bg-foreground');
    expect(src).not.toContain('surface-popover');
  });
});

describe('glass does not switch the shader off', () => {
  const GLASS = readFileSync(
    join(__dirname, '..', 'store', 'items', 'material', 'glass.css'), 'utf8')
    .replace(/\/\*[\s\S]*?\*\//g, '');

  it('every shadow it declares answers the light', () => {
    // The rule that applies this shadow out-specifies the light rung in
    // index.css — deliberately, so it can beat the `shadow-lg` a dialog
    // carries. That meant a literal here silently DISABLED the Shaders
    // axis on every card, dialog, sheet, select and context-menu, and
    // neither axis's own tests could see it: material checked its rule
    // existed, shaders checked its tokens existed, and nothing checked
    // that one read the other.
    const decls = [...GLASS.matchAll(/--surface-shadow:\s*([^;]+);/g)].map((m) => m[1]);
    expect(decls.length, 'glass declares no shadow — this checks nothing')
      .toBeGreaterThan(1);
    for (const d of decls) {
      expect(d, `a --surface-shadow with no light term: ${d.slice(0, 60)}`)
        .toMatch(/var\(--light-/);
    }
  });

  it('and the scan can fail', () => {
    expect('0 1px 2px rgb(0 0 0 / 6%)').not.toMatch(/var\(--light-/);
    expect('0 calc(1px * var(--light-lift, 1)) 2px').toMatch(/var\(--light-/);
  });
});

describe('the axis reaches every surface that is one', () => {
  /**
   * `.surface` is the class the material axis paints through — nothing
   * else is reached. A surface that writes `bg-popover` by hand keeps
   * its colour and loses the axis: pick glass, and it stays solid while
   * every dialog and card around it goes translucent.
   *
   * Twenty sites did exactly that, across eleven files, which is most of
   * what "material only covers a fifth of the product" meant.
   */
  const OCCLUDERS: Record<string, string> = {
    'components/ui/select.tsx':
      'the scroll arrows inside an already-surfaced popup. They paint the '
      + 'popover colour to OCCLUDE the list scrolling under them — the same '
      + 'reason glass.css keeps an escape hatch for sticky and pinned '
      + 'elements. A translucent occluder occludes nothing.',
  };

  const tsxFiles = (dir: string, out: string[] = []): string[] => {
    for (const name of readdirSync(dir)) {
      const full = join(dir, name);
      if (statSync(full).isDirectory()) {
        if (name !== 'node_modules') tsxFiles(full, out);
      } else if (name.endsWith('.tsx') && !name.includes('.test.')) out.push(full);
    }
    return out;
  };

  it('nothing paints the popover colour outside the axis', () => {
    const SRC = join(__dirname, '..', '..');
    const offenders: string[] = [];
    let exempt = 0;
    for (const full of tsxFiles(SRC)) {
      const rel = relative(SRC, full).split(sep).join('/');
      const src = readFileSync(full, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
      for (const m of src.matchAll(/className=(?:"([^"]*)"|\{([\s\S]{0,600}?)\}\s*(?:\n|\/?>|[a-zA-Z-]+=))/g)) {
        const body = m[2] ?? '';
        const c = m[1] ?? [...body.matchAll(/'([^']*)'|"([^"]*)"|`([^`]*)`/g)]
          .map((s) => s[1] ?? s[2] ?? s[3] ?? '').join(' ');
        if (!/\bbg-popover(?![/\w-])/.test(c)) continue;
        if (OCCLUDERS[rel]) { exempt += 1; continue; }
        if (/\bsurface\b/.test(c)) continue;
        offenders.push(`${rel}:${src.slice(0, m.index ?? 0).split('\n').length}`);
      }
    }
    expect(exempt, 'the occluder exemption names a file that no longer paints it')
      .toBeGreaterThan(0);
    expect(offenders,
      'a surface paints the popover colour and never joins the material axis — '
      + 'use `surface surface-popover` instead of `bg-popover`')
      .toEqual([]);
  });
});


/**
 * The two things that made the menu unreadable, neither of them the
 * translucency itself.
 */
describe('a surface paints its own colour, on its own backdrop', () => {
  const CSS = readFileSync(join(SRC, 'index.css'), 'utf8');

  /**
   * `--surface-base` is a CUSTOM PROPERTY, and those inherit — so a
   * `<Card>` mounted inside the sidebar painted in the RAIL's colour.
   * The persona menu is four levels down from `.surface-sidebar`; in
   * dark that made it the same lightness as the thing it covers.
   * Opaque and invisible is not better than translucent and illegible.
   *
   * Stated on the CARD, not registered with `@property
   * { inherits: false }` — which was the first fix and reached too far:
   * it changes the property's behaviour for every element in the
   * product at once, including ones nobody has written yet, to solve a
   * problem that belongs to one primitive.
   */
  it('a Card declares its own base rather than inheriting one', () => {
    expect(CSS, '`.surface-card` is gone — a Card inside the sidebar will paint in '
      + 'the rail colour instead of its own.')
      .toMatch(/\.surface-card\s*\{[^}]*--surface-base:\s*var\(--card\)/);
    const card = readFileSync(join(SRC, 'components/ui/card.tsx'), 'utf8');
    expect(card, 'the Card primitive stopped declaring its base')
      .toMatch(/cva\("surface surface-card/);
  });

  /**
   * `backdrop-filter` on an ANCESTOR makes a descendant's own filter a
   * no-op — which is why the menu showed the sidebar through itself
   * CRISP rather than smeared. The shell's content envelope wraps the
   * header and every page, and its only visible pixels are the 8px
   * gutter: carrying `.surface` bought a frosted frame and cost a
   * viewport-sized backdrop root over every card in the app.
   */
  it('and the shell envelope is not one giant backdrop root', () => {
    const shell = readFileSync(join(SRC, 'shells/AppShell.tsx'), 'utf8');
    const envelope = /className="flex-1 flex flex-col overflow-hidden[^"]*"/.exec(shell)?.[0] ?? '';
    expect(envelope, 'the content envelope moved — this reader is stale').not.toBe('');
    expect(
      /\bsurface\b/.test(envelope.replace(/surface-\w+/g, '')),
      'the shell envelope carries `.surface` again. Under Glass that is a '
        + 'viewport-sized backdrop root, and every card inside it loses its own blur '
        + 'silently — for an 8px frosted gutter.',
    ).toBe(false);
  });
});

/**
 * A PLANE INSIDE A SURFACE IS THE SAME MATERIAL AS THE SURFACE.
 *
 * `--muted`, `--secondary` and `--accent` are the tones a sub-box
 * inside a card is painted with. They are not `.surface`, so the
 * material never reached them, and a glass card showing the wallpaper
 * with a solid grey box in the middle carried two materials at once —
 * a hole rather than a plane. 341 resting sites paint one of these.
 *
 * What is guarded is not that the rules exist but HOW they are written:
 * by utility class and reading the live token. Redefining the token
 * instead would need its own base copied out of `index.css`, which is a
 * second place for a number to drift, and would change the token where
 * it is not a background at all.
 */
describe('a plane inside a surface', () => {
  const PLANES = ['muted', 'secondary', 'accent'] as const;

  it('takes the material on every inner plane, by utility class', () => {
    for (const p of PLANES) {
      const rule = new RegExp(
        `:root\\[data-material="glass"\\]\\s+\\.bg-${p}\\s*\\{([^{}]*)\\}`,
      ).exec(CODE);
      expect(rule, `no glass rule for .bg-${p}`).toBeTruthy();
      // The LIVE token, never a copied value: a literal here is a second
      // home for a number `index.css` already owns.
      expect(rule![1], `.bg-${p} does not read its own token`)
        .toMatch(new RegExp(`var\\(--${p}\\)`));
      expect(rule![1], `.bg-${p} is not translucent — the hole is still there`)
        .toMatch(/transparent/);
      expect(rule![1], `.bg-${p} hard-codes its alpha instead of the pack value`)
        .toMatch(/var\(--surface-plane-alpha\)/);
    }
  });

  it('and leaves the hover highlight solid, on purpose', () => {
    // `hover:bg-muted` is a different class and 169 sites write one. A
    // highlight under the pointer is momentary, and saying "this row,
    // now" solidly is what it is for. Asserted so that a later sweep
    // widening the selector has to argue with this sentence first.
    expect(CODE, 'the glass rules reached the hover state')
      .not.toMatch(/\[data-material="glass"\][^{}]*hover\\?:bg-/);
  });

  it('costs the solid path nothing', () => {
    // Same bargain the whole material makes: every one of these rules is
    // behind the glass attribute, so solid renders the utility it always
    // rendered.
    for (const p of PLANES) {
      const bare = new RegExp(`\\n\\.bg-${p}\\s*\\{`).exec(CODE);
      expect(bare, `.bg-${p} was redefined for every material, not just glass`).toBeNull();
    }
  });
});

/**
 * WHAT IS THE PANE'S, AND WHAT IS THE ROOM'S.
 *
 * The material had drifted into declaring almost everything twice, once
 * per mode — including two things that are properties of the MATERIAL
 * and cannot sensibly differ. How much light a pane lets through, and
 * how much it deepens the colour passing through it, belong to the
 * pane; a window is not more transparent in a dark room. They were
 * split because two separate passes each had a local reason, and
 * neither could see the other: swept afterwards, every alpha from 0.35
 * to 0.65 clears the floor in both modes, so the contrast was never
 * asking for it.
 *
 * The ones that DO differ are the room's, and the guard asserts that
 * too — otherwise this reads as "fewer per-mode values is better",
 * which would be wrong. A rim is a reflection and is only visible
 * against a ground darker than itself. A tint has to follow its ink or
 * the text cannot be read. Optics agrees: transmission and index are
 * constants of a material; a highlight is a fact about the light.
 */
describe('the material and the room', () => {
  const block = (sel: string) => {
    let body = '';
    for (const m of CODE.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1].trim().replace(/\s+/g, ' ') === sel) body += m[2];
    return body;
  };
  const LIGHT = ':root[data-material="glass"]';
  const DARK = '.dark[data-material="glass"]';

  it("the pane's own properties are declared once", () => {
    for (const tok of ['--surface-wash-page', '--surface-saturate', '--surface-blur-page']) {
      expect(block(LIGHT), `${tok} is not declared at all`).toMatch(new RegExp(`${tok}:`));
      expect(
        block(DARK),
        `${tok} is overridden per mode — it is a property of the pane, not of the room`,
      ).not.toMatch(new RegExp(`${tok}:`));
    }
  });

  it("and the room's are declared per mode, which is not the same mistake", () => {
    // The control. Without it the assertion above reads as "fewer
    // per-mode values is better" and the next edit flattens a rim that
    // has to differ — white on white is invisible, which is exactly how
    // the first version of this pack shipped a rim nobody could see in
    // light mode.
    for (const tok of ['--glass-rim-top', '--glass-rim-foot', '--glass-sheen']) {
      expect(block(LIGHT), `${tok} has no light value`).toMatch(new RegExp(`${tok}:`));
      expect(
        block(DARK),
        `${tok} stopped following the mode — a reflection is a fact about the light`,
      ).toMatch(new RegExp(`${tok}:`));
    }
  });
});

/**
 * A CLEAR PANE IS STILL A PANE.
 *
 * The fill is zero — the material paints no colour of its own, which
 * is what the owner asked for and what `glassContrast.test.ts` proves
 * the text can afford. That makes the EDGE load-bearing. A rim quietly
 * dropped to nothing, or an inset shadow reordered out of the rule,
 * would leave a surface with neither body nor boundary: not
 * transparent, absent. Nothing about the rendered result would look
 * broken in the one place anyone checks either, because a card over a
 * wallpaper still reads as a card — the wallpaper is doing it.
 *
 * The invariant is a disjunction on purpose: a pane with a real fill
 * does not need a rim to be found. It is the zero that makes the edge
 * mandatory, so the first assertion pins the zero and the rest depend
 * on it.
 */
describe('a clear pane is still a pane', () => {
  const body = (sel: string) => {
    let out = '';
    for (const m of CODE.matchAll(/([^{}]+)\{([^{}]*)\}/g))
      if (m[1].trim().replace(/\s+/g, ' ') === sel) out += m[2];
    return out;
  };
  const LIGHT = ':root[data-material="glass"]';
  const DARK = '.dark[data-material="glass"]';
  const alpha = Number(/--surface-wash-page:\s*([\d.]+)/.exec(body(LIGHT))?.[1]);

  it('paints no colour of its own', () => {
    // The owner's decision, recorded with its reason rather than left
    // as a number someone can drift back up: a wash over the whole
    // pane is what made every card read as a white board laid on the
    // wallpaper instead of a window onto it. It was measured to buy
    // 0.29 of contrast ratio in light and to COST 0.70 in dark.
    expect(Number.isFinite(alpha), 'glass states no --surface-alpha').toBe(true);
    expect(alpha, 'the pane went back to painting a wash of its own').toBeLessThan(0.05);
  });

  it('so its edge is the only thing that finds it, and must survive', () => {
    const rim = (sel: string, tok: string) => {
      const m = new RegExp(`${tok}:[^;]*?calc\\(\\s*([\\d.]+)`).exec(body(sel));
      return m ? Number(m[1]) : 0;
    };
    for (const [name, sel] of [['light', LIGHT], ['dark', DARK]] as const)
      for (const tok of ['--glass-rim-top', '--glass-rim-foot'])
        expect(rim(sel, tok), `${tok} carries no light in ${name}, and the fill is zero`)
          .toBeGreaterThan(0);

    // And the rule that spends them. A token nobody reads is the same
    // as a token set to nothing.
    const surface = body(`${LIGHT} .surface`);
    expect(surface, 'the lit top edge is no longer painted')
      .toMatch(/inset 0 1px 0 var\(--glass-rim-top\)/);
    expect(surface, 'the shaded foot is no longer painted')
      .toMatch(/inset 0 -1px 0 var\(--glass-rim-foot\)/);
  });

  it('and the border a Card already carries is part of that edge', () => {
    const card = readFileSync(join(SRC, 'components/ui/card.tsx'), 'utf8');
    expect(card, 'a clear card lost the border that was drawing its boundary')
      .toMatch(/cardVariants = cva\(\s*"[^"]*\bborder border-border\b/);
  });
});

/**
 * THE WASH IS A COLOUR AND A STRENGTH, AND IT STAYS TWO THINGS.
 *
 * This is here because of how long it took to find. The owner could
 * see a surface "going white" and say so plainly; neither of us could
 * point at the line that made it white, for hours, across several
 * sessions. The reason was not that the code was complicated. It was
 * that there was no WORD for what it did: one token named a strength,
 * the colour it applied came from the palette by inheritance, and so a
 * single declaration LIGHTENED in light mode and DARKENED in dark
 * while no line anywhere stated a direction.
 *
 * Splitting them fixes the finding, not the pixels. `--surface-wash`
 * says what colour and can be read in one glance; `--surface-wash-page`
 * and `--surface-wash-floating` say how much, per region. What these
 * guards hold is that the pair does not quietly become one thing again.
 */
describe('the wash is a colour and a strength', () => {
  const decl = (tok: string) =>
    new RegExp(`${tok}:\\s*([^;]+);`).exec(CODE)?.[1].trim();

  it('states its colour outright, never borrowing one from the palette', () => {
    const colour = decl('--surface-wash');
    expect(colour, 'the pack declares no wash colour').toBeTruthy();
    // A BORROWED COLOUR HAS NO DIRECTION. `var(--popover)` is white in
    // one mode and near-black in the other, so the same line lightens
    // and darkens depending on a token declared in a different file —
    // which is precisely the shape that cost those hours. A literal can
    // be read. A pack that genuinely wants per-mode says it twice, in
    // two blocks, where both are visible.
    expect(colour, 'the wash borrows its colour, so its DIRECTION is not stated anywhere')
      .not.toMatch(/var\(/);
  });

  it('and nothing paints a surface from anything else', () => {
    // The other half: a rule that reached for `--surface-base` or a
    // palette token directly would put the wash back inside the code
    // with no name on it, and the next person looking for "the thing
    // that makes it white" would search for a word that matches
    // nothing again.
    const fills = [...CODE.matchAll(/\.surface[^{}]*\{([^{}]*)\}/g)]
      .map((m) => m[1])
      .filter((body) => /background-color:\s*color-mix/.test(body));
    expect(fills.length, 'no surface lays a wash at all — this measures nothing')
      .toBeGreaterThan(1);
    for (const body of fills)
      expect(body, 'a surface mixes its fill from something that is not the wash')
        .toMatch(/color-mix\([^;]*var\(--surface-wash\)/);
  });

  it('and every region-split token has both of its halves', () => {
    // The vocabulary enforces itself: `-floating` means "what a surface
    // over unchosen content does instead", so it is only a difference
    // if there is a `-page` to differ from. One grep for `-floating`
    // then finds that whole difference as a list, which is the thing
    // that was missing.
    const halves = (suffix: string) => new Set(
      [...CODE.matchAll(new RegExp(`--surface-([a-z-]+?)-${suffix}\\b`, 'g'))].map((m) => m[1]),
    );
    const page = halves('page');
    const floating = halves('floating');
    expect([...page].sort(), 'a split token is missing one of its two halves')
      .toEqual([...floating].sort());
    expect(page.size, 'nothing is split by region — the suffix means nothing')
      .toBeGreaterThan(1);
  });
});
