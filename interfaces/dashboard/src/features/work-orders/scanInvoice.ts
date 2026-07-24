// Scan-invoice → form-patch mapper (pure, unit-tested).
//
// The extract endpoint returns WO-shaped fields read off an invoice;
// this module decides what actually lands in the form, enforcing the
// owner's overwrite contract: EMPTY fields fill silently, NON-EMPTY
// fields never change without explicit user approval — they come back
// as `conflicts` for the confirm dialog, with the would-be values in
// `overwrites` so approval is a plain object spread.

export interface ExtractLineItem {
  description: string;
  kind: 'part' | 'labor';
  quantity: number;
  unit_price: number;
  total: number;
}

export interface InvoiceExtract {
  fields: {
    vendor: { name: string; phone: string; email: string; address: string };
    invoice_number: string;
    service_date: string;
    odometer: number | null;
    vehicle_hint: string;
    line_items: ExtractLineItem[];
    fee: number;
    tax: number;
    total: number;
  };
  confidence: Record<string, number>;
  notes: string;
  warnings: string[];
}

export interface ScanConflict {
  /** WorkOrder field key ('vendor_name', 'tax_amount', …). */
  key: string;
  current: string;
  proposed: string;
}

export interface ScanPart {
  part_name: string;
  part_number: string;
  quantity: number;
  unit_cost: number;
  total_cost: number;
  warranty_months: number;
  service_task: string;
  notes: string;
}

export interface ScanLabor {
  description: string;
  hours: number;
  rate: number;
  total_cost: number;
  service_task: string;
}

export interface ScanPatchResult {
  /** Safe fills — every target was empty. Spread into the WO state. */
  patch: Record<string, unknown>;
  /** Non-empty targets the invoice disagrees with — needs approval. */
  conflicts: ScanConflict[];
  /** conflict key → value to apply IF the user approves that row. */
  overwrites: Record<string, unknown>;
  parts: ScanPart[];
  labor: ScanLabor[];
  /** Keys auto-filled (drives the "AI-filled" highlight + banner). */
  filled: string[];
  /** Line items existed on the invoice but the form already has lines. */
  linesSkipped: boolean;
}

interface CurrentWo {
  [key: string]: unknown;
}

interface VehicleOption {
  name: string;
  vehicle_type?: string;
}

const isBlank = (v: unknown): boolean =>
  v == null || (typeof v === 'string' && v.trim() === '') ||
  (typeof v === 'number' && v === 0);

/** Resolve the invoice's unit-number hint against the fleet — exact
 *  case-insensitive name match first, then digits-only match; only an
 *  UNAMBIGUOUS (single) match counts, anything else returns null. */
export function matchVehicleHint(
  hint: string, vehicles: VehicleOption[],
): VehicleOption | null {
  const h = hint.trim().toLowerCase();
  if (!h) return null;
  const exact = vehicles.filter(v => v.name.trim().toLowerCase() === h);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) return null;
  const hd = h.replace(/[^0-9]/g, '');
  if (!hd) return null;
  const byDigits = vehicles.filter(
    v => v.name.replace(/[^0-9]/g, '') === hd && hd.length > 0,
  );
  return byDigits.length === 1 ? byDigits[0] : null;
}

export function buildScanPatch(
  extract: InvoiceExtract,
  current: CurrentWo,
  opts: { vehicles: VehicleOption[]; hasExistingLines: boolean },
): ScanPatchResult {
  const f = extract.fields;
  const patch: Record<string, unknown> = {};
  const conflicts: ScanConflict[] = [];
  const overwrites: Record<string, unknown> = {};
  const filled: string[] = [];

  const propose = (key: string, value: unknown, display?: string) => {
    if (value == null || value === '' || value === 0) return;   // nothing read
    const cur = current[key];
    if (isBlank(cur)) {
      patch[key] = value;
      filled.push(key);
      return;
    }
    // Money fields: identical numbers aren't a conflict.
    const same = typeof value === 'number' && typeof cur === 'number'
      ? Math.abs(value - cur) < 0.005
      : String(cur).trim().toLowerCase() === String(value).trim().toLowerCase();
    if (same) return;
    conflicts.push({
      key,
      current: String(cur),
      proposed: display ?? String(value),
    });
    overwrites[key] = value;
  };

  propose('vendor_name', f.vendor.name);
  propose('vendor_phone', f.vendor.phone);
  propose('vendor_email', f.vendor.email);
  propose('vendor_address', f.vendor.address);
  propose('invoice_number', f.invoice_number);
  propose('service_date', f.service_date);
  propose('odometer_at_service', f.odometer);
  propose('fee_amount', f.fee);
  propose('tax_amount', f.tax);

  // Vehicle: only via an unambiguous fleet match — the invoice's "102"
  // must resolve to exactly one registered unit before we suggest it.
  const match = matchVehicleHint(f.vehicle_hint || '', opts.vehicles);
  if (match) {
    if (isBlank(current.vehicle_name)) {
      patch.vehicle_name = match.name;
      if (match.vehicle_type && isBlank(current.vehicle_type)) {
        patch.vehicle_type = match.vehicle_type;
      }
      filled.push('vehicle_name');
    } else if (
      String(current.vehicle_name).trim().toLowerCase()
        !== match.name.trim().toLowerCase()
    ) {
      conflicts.push({
        key: 'vehicle_name',
        current: String(current.vehicle_name),
        proposed: match.name,
      });
      overwrites.vehicle_name = match.name;
    }
  }

  // Line items land only in an empty editor — merging AI lines into a
  // hand-built list risks silent duplicates, so a non-empty form gets a
  // banner note instead ("linesSkipped").
  const parts: ScanPart[] = [];
  const labor: ScanLabor[] = [];
  let linesSkipped = false;
  if (f.line_items.length && opts.hasExistingLines) {
    linesSkipped = true;
  } else {
    for (const li of f.line_items) {
      if (li.kind === 'labor') {
        labor.push({
          description: li.description,
          hours: 0,
          rate: 0,
          total_cost: li.total,
          service_task: '',
        });
      } else {
        parts.push({
          part_name: li.description,
          part_number: '',
          quantity: li.quantity || 1,
          unit_cost: li.unit_price,
          total_cost: li.total,
          warranty_months: 0,
          service_task: '',
          notes: '',
        });
      }
    }
  }

  return { patch, conflicts, overwrites, parts, labor, filled, linesSkipped };
}
