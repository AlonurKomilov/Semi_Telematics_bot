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
  it('no longer promises editing over there, because editing is here', () => {
    expect(panel).not.toContain('Edit or retire on 4truck');
    expect(panel).toContain('Retire or move on 4truck →');
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
