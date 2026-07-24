// buildScanPatch — the owner's overwrite contract, in executable form:
// empty fields fill silently, non-empty fields NEVER change without
// showing up as a conflict, vehicle hints only resolve unambiguously.
import { describe, it, expect } from 'vitest';
import { buildScanPatch, matchVehicleHint, type InvoiceExtract } from './scanInvoice';

const extract = (over: Partial<InvoiceExtract['fields']> = {}): InvoiceExtract => ({
  fields: {
    vendor: { name: 'Auto Glass Factory LLC', phone: '555-1234', email: 'shop@x.com', address: '1 Main St, Dallas, TX' },
    invoice_number: 'INV-88',
    service_date: '2026-07-20',
    odometer: 247500,
    vehicle_hint: '234',
    line_items: [
      { description: 'DD15 Oil Filter', kind: 'part', quantity: 2, unit_price: 45, total: 90 },
      { description: 'Diagnostics', kind: 'labor', quantity: 1, unit_price: 120, total: 120 },
    ],
    fee: 15,
    tax: 8.5,
    total: 233.5,
    ...over,
  },
  confidence: { vendor: 1, line_items: 1, totals: 1, meta: 1 },
  notes: '',
  warnings: [],
});

const blank = {
  vehicle_name: '', vehicle_type: '', vendor_name: '', vendor_phone: '',
  vendor_email: '', vendor_address: '', invoice_number: '', service_date: '',
  odometer_at_service: null, fee_amount: 0, tax_amount: 0,
};

const vehicles = [
  { name: '234', vehicle_type: 'truck' },
  { name: 'TRL-88', vehicle_type: 'trailer' },
];

describe('buildScanPatch', () => {
  it('fills every empty field silently, with no conflicts', () => {
    const r = buildScanPatch(extract(), blank, { vehicles, hasExistingLines: false });
    expect(r.conflicts).toEqual([]);
    expect(r.patch).toMatchObject({
      vendor_name: 'Auto Glass Factory LLC',
      vendor_phone: '555-1234',
      invoice_number: 'INV-88',
      service_date: '2026-07-20',
      odometer_at_service: 247500,
      fee_amount: 15,
      tax_amount: 8.5,
      vehicle_name: '234',
      vehicle_type: 'truck',
    });
    expect(r.filled).toContain('vendor_name');
    expect(r.filled).toContain('vehicle_name');
  });

  it('splits line items into parts and labor drafts', () => {
    const r = buildScanPatch(extract(), blank, { vehicles, hasExistingLines: false });
    expect(r.parts).toHaveLength(1);
    expect(r.parts[0]).toMatchObject({ part_name: 'DD15 Oil Filter', quantity: 2, unit_cost: 45, total_cost: 90 });
    expect(r.labor).toHaveLength(1);
    expect(r.labor[0]).toMatchObject({ description: 'Diagnostics', total_cost: 120 });
  });

  it('NEVER silently overwrites a non-empty field — it becomes a conflict', () => {
    const cur = { ...blank, vendor_name: 'Old Shop', tax_amount: 5 };
    const r = buildScanPatch(extract(), cur, { vehicles, hasExistingLines: false });
    expect(r.patch.vendor_name).toBeUndefined();
    expect(r.patch.tax_amount).toBeUndefined();
    const keys = r.conflicts.map(c => c.key);
    expect(keys).toContain('vendor_name');
    expect(keys).toContain('tax_amount');
    expect(r.overwrites.vendor_name).toBe('Auto Glass Factory LLC');
    expect(r.overwrites.tax_amount).toBe(8.5);
  });

  it('an identical existing value is neither a fill nor a conflict', () => {
    const cur = { ...blank, vendor_name: 'auto glass factory llc', tax_amount: 8.5 };
    const r = buildScanPatch(extract(), cur, { vehicles, hasExistingLines: false });
    expect(r.patch.vendor_name).toBeUndefined();
    expect(r.conflicts.map(c => c.key)).not.toContain('vendor_name');
    expect(r.conflicts.map(c => c.key)).not.toContain('tax_amount');
  });

  it('skips line items when the form already has lines, and says so', () => {
    const r = buildScanPatch(extract(), blank, { vehicles, hasExistingLines: true });
    expect(r.parts).toEqual([]);
    expect(r.labor).toEqual([]);
    expect(r.linesSkipped).toBe(true);
  });

  it('unreadable values ("", 0, null) never fill or conflict', () => {
    const r = buildScanPatch(
      extract({ vendor: { name: '', phone: '', email: '', address: '' }, invoice_number: '', service_date: '', odometer: null, fee: 0, tax: 0, vehicle_hint: '' }),
      { ...blank, vendor_name: 'Old Shop' },
      { vehicles, hasExistingLines: false },
    );
    expect(Object.keys(r.patch)).toEqual([]);
    expect(r.conflicts).toEqual([]);
  });

  it('a different existing vehicle becomes a conflict, not a swap', () => {
    const cur = { ...blank, vehicle_name: 'TRL-88' };
    const r = buildScanPatch(extract(), cur, { vehicles, hasExistingLines: false });
    expect(r.patch.vehicle_name).toBeUndefined();
    expect(r.conflicts.find(c => c.key === 'vehicle_name')?.proposed).toBe('234');
  });
});

describe('matchVehicleHint', () => {
  it('exact case-insensitive name match wins', () => {
    expect(matchVehicleHint('trl-88', vehicles)?.name).toBe('TRL-88');
  });
  it('digits-only match resolves prefixed hints', () => {
    expect(matchVehicleHint('TRK-234', vehicles)?.name).toBe('234');
  });
  it('ambiguity returns null', () => {
    const dupes = [{ name: 'TRK-102' }, { name: 'TRL-102' }];
    expect(matchVehicleHint('102', dupes)).toBeNull();
  });
  it('an exact name beats a digits-only sibling', () => {
    const fleet = [{ name: '102' }, { name: 'TRK-102' }];
    expect(matchVehicleHint('102', fleet)?.name).toBe('102');
  });
  it('empty or digit-less hints return null', () => {
    expect(matchVehicleHint('', vehicles)).toBeNull();
    expect(matchVehicleHint('N/A', vehicles)).toBeNull();
  });
});
