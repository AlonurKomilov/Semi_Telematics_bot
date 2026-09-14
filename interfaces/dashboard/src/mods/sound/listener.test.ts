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
    // And the forwarded event is the one that would speak, if its
    // control were not still waiting for a name.
    expect(classifyAct(at('<label><input type="checkbox" data-probe /> Ok</label>')))
      .toBeNull();
  });
});

describe('what is waiting for its own name', () => {
  /**
   * These are SILENT rather than sounding `press`, and the reason is
   * that a cue somebody has learned cannot be quietly reassigned. Giving
   * a toggle the press sound today would teach a meaning that has to be
   * un-taught the moment `toggle_on` ships.
   */
  const deferred: ReadonlyArray<readonly [string, string]> = [
    ['a switch', '<button role="switch" aria-checked="false" data-probe>x</button>'],
    ['a chip', '<button aria-pressed="false" data-probe>Chime</button>'],
    ['a tab', '<button role="tab" data-probe>All</button>'],
    ['a checkbox', '<input type="checkbox" data-probe />'],
    ['the checkbox primitive', '<input data-slot="checkbox" data-probe />'],
    ['a radio', '<input type="radio" data-probe />'],
    ['a select item', '<div role="option" data-slot="select-item" data-probe>25</div>'],
    ['a menu item', '<div role="menuitem" data-probe>Delete</div>'],
  ];
  for (const [what, html] of deferred) {
    it(`${what} is silent until it has its own cue`, () => {
      expect(classifyAct(at(html))).toBeNull();
    });
  }

  /** The one that would otherwise slip through: a switch is a
   *  `<button>`, so the deferral has to beat the press rule. */
  it('and the order is what makes that true', () => {
    expect(classifyAct(at('<button role="switch" data-probe><span>x</span></button>')))
      .toBeNull();
  });
});

describe('the scan can fail', () => {
  it('on nothing, and on something that is not an element', () => {
    expect(classifyAct(null)).toBeNull();
    expect(classifyAct({} as EventTarget)).toBeNull();
  });
});
