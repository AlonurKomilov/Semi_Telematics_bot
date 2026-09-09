import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../api/client', () => ({ apiFetch: vi.fn() }));

import { apiFetch } from '../../api/client';
import {
  forgetInventory, humanize, inventoryFor, isAttention, sortForPanel, statusTone,
  type InventoryItem,
} from './data';

const mocked = vi.mocked(apiFetch);

function reply(body: unknown, status = 200): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response;
}

const item = (id: number, status: string, label = `item ${id}`): InventoryItem =>
  ({ id, category: 'other', label, status });

beforeEach(() => {
  forgetInventory();
  mocked.mockReset();
});

describe('what the panel shows first', () => {
  it('puts the worst status on top and keeps the server order under it', () => {
    const rows = [
      item(1, 'installed'), item(2, 'needs_check'), item(3, 'missing'),
      item(4, 'installed'), item(5, 'damaged'), item(6, 'in_repair'),
    ];
    expect(sortForPanel(rows).map((i) => i.status)).toEqual([
      'missing', 'damaged', 'in_repair', 'needs_check', 'installed', 'installed',
    ]);
    // Equal rank keeps the order it arrived in — the server already
    // sorted by category, label, id, and re-sorting would fight it.
    expect(sortForPanel(rows).filter((i) => i.status === 'installed').map((i) => i.id))
      .toEqual([1, 4]);
  });

  it('does not reorder a list that has nothing wrong with it', () => {
    const rows = [item(3, 'installed'), item(1, 'spare'), item(2, 'installed')];
    expect(sortForPanel(rows).map((i) => i.id)).toEqual([3, 1, 2]);
  });

  it('agrees with the server about which statuses want somebody', () => {
    for (const s of ['missing', 'damaged', 'in_repair', 'needs_check']) {
      expect(isAttention(s), s).toBe(true);
    }
    for (const s of ['installed', 'spare', 'whatever_comes_next']) {
      expect(isAttention(s), s).toBe(false);
    }
  });

  it('colours a status by how much it wants somebody', () => {
    expect(statusTone('missing')).toBe('danger');
    expect(statusTone('damaged')).toBe('danger');
    expect(statusTone('in_repair')).toBe('warn');
    expect(statusTone('needs_check')).toBe('warn');
    expect(statusTone('installed')).toBe('ok');
    // The category vocabulary is open and a status the panel has never
    // heard of must render as "nothing claimed", never as an alarm.
    expect(statusTone('spare')).toBe('muted');
    expect(statusTone('invented_by_an_account')).toBe('muted');
  });
});

describe('humanize', () => {
  it('reads snake_case as a sentence', () => {
    expect(humanize('needs_check')).toBe('Needs check');
    expect(humanize('fuel_card')).toBe('Fuel card');
    expect(humanize('toll_transponder')).toBe('Toll transponder');
    expect(humanize('safety_equipment')).toBe('Safety equipment');
  });

  it('keeps the acronyms that would otherwise read as words', () => {
    expect(humanize('eld')).toBe('ELD');
    expect(humanize('gps')).toBe('GPS');
  });

  it('survives nothing at all', () => {
    expect(humanize('')).toBe('');
  });
});

describe('inventoryFor', () => {
  it('asks once inside the window and again after it', async () => {
    mocked.mockResolvedValue(reply({ items: [item(1, 'installed')], attention: 0 }));
    expect((await inventoryFor(7, 1000))?.items).toHaveLength(1);
    await inventoryFor(7, 1000 + 59_999);
    expect(mocked).toHaveBeenCalledTimes(1);
    // Somebody may have marked a dashcam missing while the panel sat open.
    await inventoryFor(7, 1000 + 60_001);
    expect(mocked).toHaveBeenCalledTimes(2);
  });

  it('keeps one truck out of another truck’s answer', async () => {
    mocked.mockResolvedValueOnce(reply({ items: [item(1, 'missing')], attention: 1 }));
    mocked.mockResolvedValueOnce(reply({ items: [item(2, 'installed')], attention: 0 }));
    expect((await inventoryFor(1, 1000))?.attention).toBe(1);
    expect((await inventoryFor(2, 1000))?.attention).toBe(0);
    expect((await inventoryFor(1, 1000))?.attention).toBe(1);
    expect(mocked).toHaveBeenCalledTimes(2);
  });

  it('stops asking entirely once the answer is "you may not"', async () => {
    mocked.mockResolvedValue(reply({ detail: 'Insufficient permissions' }, 403));
    expect(await inventoryFor(1, 1000)).toBeNull();
    // A grant the owner withheld is the same answer for every truck, so
    // the panel must not spend a request per selection discovering it.
    expect(await inventoryFor(2, 2000)).toBeNull();
    expect(await inventoryFor(3, 3000)).toBeNull();
    expect(mocked).toHaveBeenCalledTimes(1);
  });

  it('asks again after a disconnect — the next key may be somebody else’s', async () => {
    mocked.mockResolvedValueOnce(reply({}, 403));
    expect(await inventoryFor(1, 1000)).toBeNull();
    forgetInventory();
    mocked.mockResolvedValueOnce(reply({ items: [item(1, 'installed')], attention: 0 }));
    expect((await inventoryFor(1, 2000))?.items).toHaveLength(1);
    expect(mocked).toHaveBeenCalledTimes(2);
  });

  it('does not remember a failure', async () => {
    mocked.mockResolvedValueOnce(reply({}, 503));
    expect(await inventoryFor(1, 1000)).toBeNull();
    mocked.mockResolvedValueOnce(reply({ items: [item(1, 'spare')], attention: 0 }));
    expect((await inventoryFor(1, 1000))?.items).toHaveLength(1);
    expect(mocked).toHaveBeenCalledTimes(2);
  });

  it('costs the card nothing when the API is unreachable', async () => {
    mocked.mockRejectedValue(new Error('offline'));
    await expect(inventoryFor(1, 1000)).resolves.toBeNull();
  });

  it('asks nothing at all for a truck with no registry row', async () => {
    expect(await inventoryFor(null)).toBeNull();
    expect(await inventoryFor(undefined)).toBeNull();
    expect(mocked).not.toHaveBeenCalled();
  });

  it('survives a payload that is missing its fields', async () => {
    mocked.mockResolvedValue(reply({}));
    expect(await inventoryFor(1, 1000)).toEqual({ items: [], attention: 0 });
  });
});
