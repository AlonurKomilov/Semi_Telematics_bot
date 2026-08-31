/**
 * The document vocabulary, in one place.
 *
 * Two surfaces render these — the per-truck card and the fleet page —
 * and a label map copied into both is a label map that drifts: the
 * page would still say "Annual inspection" a month after the card
 * started saying something else.  Mirrors ``VEHICLE_DOC_TYPES`` in
 * adapters/storage/vehicle_documents.py, which is the wire authority;
 * the server still validates, so a key missing here degrades to the
 * raw value rather than a broken upload.
 */
export const TYPE_LABEL: Record<string, string> = {
  registration: 'Registration',
  cab_card: 'Cab card',
  title: 'Title',
  insurance: 'Insurance',
  annual_inspection: 'Annual inspection',
  ifta: 'IFTA',
  permit: 'Permit',
  emissions: 'Emissions',
  lease: 'Lease',
  purchase: 'Purchase',
  warranty: 'Warranty',
  other: 'Other',
};

/** Display order when the server has not spoken yet. */
export const TYPE_ORDER: string[] = Object.keys(TYPE_LABEL);

export const typeLabel = (key: string): string => TYPE_LABEL[key] ?? key;
