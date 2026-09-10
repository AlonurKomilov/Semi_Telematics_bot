/**
 * Correcting an item from the panel — the rules that make it safe.
 *
 * Editing was kept OUT of the panel for one reason: a rename is the
 * quietest way to make a loss disappear, and the trail recorded only
 * the bare fact "edited", with none of the words it replaced.  The
 * server now records them, so the control could come here.
 *
 * That trade is only honoured while three things stay true, and none of
 * them can be asserted by rendering — two are about which control is
 * offered to whom, and one is about what travels on the wire.
 */
import { describe, expect, it } from 'vitest';

import rowsSrc from './ItemRows.tsx?raw';
import panelSrc from './InventoryPanel.tsx?raw';
import dataSrc from './data.ts?raw';

const rows = rowsSrc as unknown as string;
const panel = panelSrc as unknown as string;
const data = dataSrc as unknown as string;

describe('the edit control', () => {
  it('is offered only to a caller that was given the verb', () => {
    // The map card renders the same rows READ-ONLY.  A control shown
    // there would be a promise the server answers 403 to — and the
    // panel decides by `abilities`, which is the server's own word.
    expect(rows).toContain('{onEdit && (');
    expect(panel).toContain('onEdit={canWrite ?');
    expect(panel).toContain("canWrite = abilities.includes('inventory.write')");
  });

  it('puts BOTH actions on the row, and leaves the strip to facts', () => {
    // Edit used to live inside the strip, so correcting a record cost
    // two presses and the row's trailing slot was spent on "Installed"
    // — the word the green dot already says.  Verify then sat one line
    // below Edit, which read as two different kinds of thing when they
    // are the same kind.
    expect(rows).toContain('className="btn compact"');
    // The row is a CONTAINER: a button inside a button is invalid HTML
    // that keyboards and screen readers cannot untangle.
    expect(rows).toContain('<div className="row" style={{ gap: 6 }}>');
    // Verify appears only while the row is OPEN — 190 closed rows each
    // offering two actions is a wall, and a check is not something you
    // press before seeing how long it has been.
    expect(rows).toContain('{open && onVerify && (');
    // The strip carries no button of its own any more: it is the serial
    // and the age, which is what the check is made against.
    const strip = rows.slice(rows.indexOf('This line is FACTS now'),
                             rows.indexOf('{onStatus && ('));
    expect(strip).not.toContain('<button');
  });

  it('gives the trailing slot up only where the dot can say it', () => {
    // `installed` IS what a green dot means, so the word was the same
    // fact twice.  `missing` and `damaged` both draw a RED dot — there
    // the word is the only thing separating "broken" from "gone".
    expect(rows).toContain("it.status === 'installed' ? '' : humanize(it.status)");
  });

  it('lets a form have its own height instead of squeezing it', () => {
    // The form is ~200px; the list's ceiling is sized for rows and drops
    // to 96 while Add is open.  Inside it the form became a ~90px
    // scroller showing one field at a time, with its own scrollbar.
    expect(panel).toContain('maxHeight={editing ? null : (adding ? 96 : 280)}');
    expect(panel).toContain('onEditingChange');
    // Two forms at once is a wall at 320px — and it is what made the
    // squeeze visible in the first place.
    expect(panel).toContain('if (on) setAdding(false);');
    expect(rows).toContain('onEditingChange?.(id !== null)');
  });

  it('replaces the strip instead of adding a third line to it', () => {
    // At the panel's 320px floor a form plus five controls is a wall,
    // and the two are different jobs: reporting what you found, and
    // fixing what the record says.
    expect(rows).toContain('open && editId === it.id && onEdit');
    expect(rows).toContain('open && editId !== it.id');
  });

  it('sends only the fields that actually changed', () => {
    // An edit that stamped all three would fill the accountability
    // trail with rows recording that nothing happened — which is how a
    // reader learns to skim past the rows where something did.
    expect(data).toContain('if (patch[k] !== undefined) body[k] = patch[k];');
    expect(data).toContain("'/extension/inventory-edit'");
  });
});

describe('what the panel still sends to the desk', () => {
  it('sends nobody to the dashboard from the vehicle card', () => {
    // The card carried a hand-off link only because correcting a record
    // was a dashboard errand.  It is not any more, so the link pointed
    // at retiring and moving alone — desk actions nobody opens this
    // panel to perform, costing a line in a 320px column on every view.
    for (const gone of ['Edit or retire on 4truck', 'Retire or move on 4truck']) {
      expect(panel).not.toContain(gone);
    }
  });

  it('still shows the way out when the panel itself can do nothing', () => {
    // The ONE hand-off that survives: a reader with no write grant
    // looking at an account where nothing has been recorded anywhere.
    // Telling them to press Add item would offer a button they do not
    // have — the empty state says it only to somebody who does.
    expect(panel).toContain('Open Inventory on 4truck →');
    expect(panel).toContain('{!canWrite && (');
    expect(panel).toContain('Pick one below and press Add item.');
  });

  it('offers no retire and no transfer of its own', () => {
    // The server shuts these two doors as well (EXTENSION_ROUTES), so
    // this is the near half of a wall with two sides.
    for (const gone of ['inventory-remove', 'inventory-transfer']) {
      expect(data).not.toContain(gone);
      expect(panel).not.toContain(gone);
    }
  });
});
