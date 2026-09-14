/**
 * What a click IS, read from the DOM and never from the component.
 *
 * The obvious build is a cue inside `components/ui/button.tsx`, and the
 * measurement rules it out: this codebase ships 278 `<Button>` and 690
 * raw `<button>`. A cue in the primitive reaches under a third of the
 * clickable things on screen, and the two thirds it misses cluster by
 * feature and by who wrote the page — which is the one outcome the
 * owner ruled out, the same act sounding here and silent there.
 *
 * So the classifier matches tags, roles and slots. Every case below is a
 * shape this product actually ships; the suppression half matters more
 * than the coverage half, because a sound with no act behind it is
 * indistinguishable from a fault.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { classifyAct } from './listener';

const root = document.createElement('div');
document.body.appendChild(root);
afterEach(() => { root.innerHTML = ''; });

/** Build markup and hand back the element to classify. */
function at(html: string, selector = '[data-probe]'): Element {
  root.innerHTML = html;
  const el = root.querySelector(selector);
  if (!el) throw new Error(`no ${selector} in ${html}`);
  return el;
}

describe('what sounds', () => {
  it('a raw button — the two thirds a primitive would have missed', () => {
    expect(classifyAct(at('<button data-probe>Save</button>'))).toBe('press');
  });

  it('the Button primitive, by its slot', () => {
    expect(classifyAct(at('<button data-slot="button" data-probe>Save</button>')))
      .toBe('press');
  });

  it('a link, a summary, and anything wearing the button role', () => {
    expect(classifyAct(at('<a href="/x" data-probe>Go</a>'))).toBe('press');
    expect(classifyAct(at('<details><summary data-probe>More</summary></details>')))
      .toBe('press');
    expect(classifyAct(at('<div role="button" data-probe>Go</div>'))).toBe('press');
  });

  /** Every button in this product wraps an icon or a label, so the
   *  click almost never lands on the button itself. */
  it('and the icon inside one, because that is what the click hits', () => {
    expect(classifyAct(at('<button><svg data-probe></svg></button>'))).toBe('press');
  });
});

describe('what stays silent', () => {
  it('a plain element with no role, however clickable it looks', () => {
    expect(classifyAct(at('<span data-probe>Clickable</span>'))).toBeNull();
    expect(classifyAct(at('<a data-probe>No href</a>'))).toBeNull();
  });

  it('a disabled control — the element itself, never an ancestor', () => {
    expect(classifyAct(at('<button disabled data-probe>Save</button>'))).toBeNull();
    expect(classifyAct(at('<button aria-disabled="true" data-probe>Save</button>')))
      .toBeNull();
    // A disabled fieldset legitimately contains controls that are live:
    // checking up the tree would silence them.
    expect(classifyAct(at('<fieldset aria-disabled="true"><button data-probe>Go</button></fieldset>')))
      .toBe('press');
  });

  it('anything marked silent by name', () => {
    expect(classifyAct(at('<div data-cue="none"><button data-probe>x</button></div>')))
      .toBeNull();
  });

  /**
   * The FMCSA form, reusing `isSensitiveTarget` rather than restating
   * it. That marker is what keeps the SSN and date-of-birth fields
   * quiet, and a second rule here would be a second thing to keep in
   * step with it.
   */
  it('and everything inside a field marked not to speak', () => {
    expect(classifyAct(at('<div data-no-key-sound><button data-probe>Next</button></div>')))
      .toBeNull();
  });

  /**
   * 292 labels in this tree wrap their own control, and `radio.tsx`
   * mandates the pattern — so a click on the label word arrives TWICE,
   * once on the label and once forwarded to the control.
   *
   * There is no special case for it, and a mutation is what proved
   * there should not be: `closest` walks UP and the control is a CHILD,
   * so the label's own event classifies to nothing by itself, and the
   * two arrive in the same task where the 90ms floor collapses them.
   * This asserts the first half — the half that makes the rest safe.
   */
  it('a label classifies to nothing on its own, so one act stays one', () => {
    expect(classifyAct(at('<label data-probe><input type="text" /> Name</label>')))
      .toBeNull();
    // And the forwarded event is the one that speaks — exactly once,
    // because the label's own event said nothing.
    expect(classifyAct(at('<label><input type="checkbox" data-probe /> Ok</label>')))
      .toBe('toggle_off');
  });
});

describe('a toggle says which way it went', () => {
  /**
   * The two kinds disagree about WHEN the state changes, and reading
   * them the same way gets one of them backwards on every single click.
   * A native input has already flipped by the time the click event
   * dispatches; an ARIA toggle has not, because React writes the new
   * attribute on its next render.
   */
  it('reads a native input as the state it is already in', () => {
    const box = at('<input type="checkbox" data-probe />') as HTMLInputElement;
    box.checked = true;
    expect(classifyAct(box)).toBe('toggle_on');
    box.checked = false;
    expect(classifyAct(box)).toBe('toggle_off');
  });

  it('and an ARIA toggle as the state it is ABOUT to be in', () => {
    // `aria-checked` here is still the old value — the act is the flip,
    // so the cue is its destination.
    expect(classifyAct(at('<button role="switch" aria-checked="false" data-probe>x</button>')))
      .toBe('toggle_on');
    expect(classifyAct(at('<button role="switch" aria-checked="true" data-probe>x</button>')))
      .toBe('toggle_off');
  });

  it('the checkbox and radio primitives, by their slot', () => {
    expect(classifyAct(at('<input type="checkbox" data-slot="checkbox" data-probe />')))
      .toBe('toggle_off');
    expect(classifyAct(at('<input type="radio" data-slot="radio" data-probe />')))
      .toBe('chip');
  });

  /** A row tick is 100-400 a shift and the tick is already on screen
   *  where the hand is looking. This is the marker that keeps the
   *  grid usable. */
  it('and a control marked silent stays silent whichever way it went', () => {
    const box = at('<input type="checkbox" data-cue="none" data-probe />') as HTMLInputElement;
    box.checked = true;
    expect(classifyAct(box)).toBeNull();
  });
});

describe('one of a visible set is a chip', () => {
  const chips: ReadonlyArray<readonly [string, string]> = [
    ['the Chip primitive', '<button aria-pressed="false" data-probe>Chime</button>'],
    ['a pressed one', '<button aria-pressed="true" data-probe>Chime</button>'],
    ['a tab', '<button role="tab" data-probe>All</button>'],
    ['a native radio', '<input type="radio" data-probe />'],
  ];
  for (const [what, html] of chips) {
    it(what, () => { expect(classifyAct(at(html))).toBe('chip'); });
  }

  /**
   * A chip-shaped control with no signature is a PRESS, and that is the
   * honest answer rather than a gap: an element that tells no assistive
   * technology it is selected is not a chip to anything that reads the
   * page. The sound follows the semantics, so both are wrong together
   * and one fix repairs both.
   */
  it('but a chip that never says it is one reads as a press', () => {
    expect(classifyAct(at('<button class="rounded-md px-2" data-probe>Chime</button>')))
      .toBe('press');
  });
});

describe('a choice out of an open list', () => {
  it('a select item and a menu item', () => {
    expect(classifyAct(at('<div role="option" data-slot="select-item" data-probe>25</div>')))
      .toBe('menu_pick');
    expect(classifyAct(at('<div role="menuitem" data-probe>Delete</div>'))).toBe('menu_pick');
    expect(classifyAct(at('<div role="menuitemcheckbox" data-probe>Show</div>')))
      .toBe('menu_pick');
  });
});

describe('order is the rule', () => {
  /**
   * A switch is a `<button>` and a tab usually is too. If the general
   * shape answered first, every specific one would be a press and the
   * vocabulary would collapse to a single sound.
   */
  it('the specific shape answers before the general one', () => {
    expect(classifyAct(at('<button role="switch" aria-checked="false" data-probe><span>x</span></button>')))
      .toBe('toggle_on');
    expect(classifyAct(at('<button role="tab" data-probe>All</button>'))).toBe('chip');
    expect(classifyAct(at('<button role="menuitem" data-probe>Del</button>')))
      .toBe('menu_pick');
  });

  it('and the icon inside a switch resolves to the switch, not the page', () => {
    expect(classifyAct(at('<button role="switch" aria-checked="true"><svg data-probe></svg></button>')))
      .toBe('toggle_off');
  });
});

describe('the scan can fail', () => {
  it('on nothing, and on something that is not an element', () => {
    expect(classifyAct(null)).toBeNull();
    expect(classifyAct({} as EventTarget)).toBeNull();
  });
});

/**
 * The three checkboxes that must not tick.
 *
 * The classifier reads the DOM, which is what makes it impossible for a
 * feature to be inconsistent — and it is also why one markup change can
 * make the busiest surface in the product unbearable. A row tick is a
 * hundred to four hundred acts in a shift; the DataGrid's per-row box
 * is one line away from being the loudest thing anybody owns.
 *
 * Two of the three are marked TEMPORARILY, and the difference matters:
 * select-all and the group box are acts that deserve a cue — one for a
 * set gathered, one for a set dropped — and they are silent only until
 * those names exist. The row box is silent for good.
 *
 * Asserted from the SOURCE rather than by rendering the grid, because
 * what is being guarded is the attribute's presence on three specific
 * renderers, and a render test would pass with the attribute moved to
 * the wrong one.
 */
describe('the grid does not tick once per row', () => {
  const SRC = readFileSync(
    join(__dirname, '..', '..', 'components', 'datagrid', 'DataGrid.tsx'), 'utf8');

  /** One renderer's body, from its declaration to the next one. */
  const bodyOf = (name: string) => {
    const at = SRC.indexOf(`const ${name} = `);
    expect(at, `${name} is gone — this reader is stale`).toBeGreaterThan(-1);
    return SRC.slice(at, at + 900);
  };

  for (const name of ['renderRowBox', 'renderSelectAll', 'renderGroupBox']) {
    it(`${name} is marked silent`, () => {
      expect(
        bodyOf(name),
        `${name} lost its \`data-cue="none"\`. The interaction listener reads the `
          + 'DOM, so an unmarked checkbox here sounds on every click — and the row '
          + 'box alone is 100-400 of them a shift.',
      ).toContain('data-cue="none"');
    });
  }

  it('and the marker is what the classifier honours', () => {
    // The positive control for all three: if `data-cue="none"` ever
    // stops suppressing, the assertions above are checking a string
    // nothing reads.
    const box = at('<input type="checkbox" data-cue="none" data-probe />');
    expect(classifyAct(box)).toBeNull();
    expect(classifyAct(at('<input type="checkbox" data-probe />'))).toBe('toggle_off');
  });
});
