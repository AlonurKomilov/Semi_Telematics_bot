// Public driver-application form — shared constants, validators, helpers.
//
// Ported from the hand-built no-build form in data/assets/*, re-typed and
// re-styled to the dashboard design system.  This bundle is mounted
// standalone on apply.<apex> (no auth / shell / router) — see main.tsx.

// The form's data object is deeply nested and built incrementally, so we
// address it by dotted/indexed path rather than a rigid type.  A loose
// record keeps the port faithful to the original while staying type-clean.
export type Data = Record<string, any>;
export type Errors = Record<string, string>;

// ── path get/set (immutable) ────────────────────────────────────────
export function deepGet(obj: Data, path: string): any {
  return path.split('.').reduce<any>((cur, k) => (cur == null ? undefined : cur[k]), obj);
}

export function deepSet(obj: Data, path: string, value: unknown): Data {
  const keys = path.split('.');
  const next: Data = Array.isArray(obj) ? obj.slice() : { ...obj };
  let cur = next;
  for (let i = 0; i < keys.length - 1; i++) {
    const k = keys[i];
    const v = cur[k];
    cur[k] = v && typeof v === 'object' && !Array.isArray(v) ? { ...v }
      : Array.isArray(v) ? v.slice() : {};
    cur = cur[k];
  }
  cur[keys[keys.length - 1]] = value;
  return next;
}

// ── input formatters ────────────────────────────────────────────────
export function formatPhone(v: string): string {
  const d = (v || '').replace(/\D/g, '').slice(0, 10);
  if (d.length < 4) return d;
  if (d.length < 7) return `(${d.slice(0, 3)}) ${d.slice(3)}`;
  return `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
}
export function formatSsn(v: string): string {
  const d = (v || '').replace(/\D/g, '').slice(0, 9);
  if (d.length < 4) return d;
  if (d.length < 6) return `${d.slice(0, 3)}-${d.slice(3)}`;
  return `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;
}

// ── validators ──────────────────────────────────────────────────────
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const ZIP_RE = /^\d{5}(-\d{4})?$/;
const SSN_RE = /^\d{3}-?\d{2}-?\d{4}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

export const V = {
  required: (v: any): string | null =>
    v == null || (typeof v === 'string' && !v.trim()) ? 'Required' : null,
  email: (v: string): string | null => (v && !EMAIL_RE.test(v.trim()) ? 'Invalid email' : null),
  phone: (v: string): string | null =>
    (v || '').replace(/\D/g, '').length >= 10 ? null : 'Invalid phone',
  zip: (v: string): string | null => (v && !ZIP_RE.test(v.trim()) ? 'Invalid ZIP' : null),
  ssn: (v: string): string | null => (v && !SSN_RE.test(v.trim()) ? 'Invalid SSN' : null),
  date: (v: string): string | null => (v && !DATE_RE.test(v) ? 'Invalid date' : null),
  minAge: (years: number) => (v: string): string | null => {
    if (!v || !DATE_RE.test(v)) return null;
    const dob = new Date(v + 'T00:00:00');
    const cutoff = new Date();
    cutoff.setFullYear(cutoff.getFullYear() - years);
    return dob <= cutoff ? null : `Must be ${years}+`;
  },
};

export function run(value: any, checks: Array<(v: any) => string | null>): string | null {
  for (const c of checks) {
    const e = c(value);
    if (e) return e;
  }
  return null;
}

// ── option constants ────────────────────────────────────────────────
export const US_STATES = [
  'AL', 'AK', 'AZ', 'AR', 'CA', 'CO', 'CT', 'DE', 'DC', 'FL', 'GA', 'HI', 'ID',
  'IL', 'IN', 'IA', 'KS', 'KY', 'LA', 'ME', 'MD', 'MA', 'MI', 'MN', 'MS', 'MO',
  'MT', 'NE', 'NV', 'NH', 'NJ', 'NM', 'NY', 'NC', 'ND', 'OH', 'OK', 'OR', 'PA',
  'RI', 'SC', 'SD', 'TN', 'TX', 'UT', 'VT', 'VA', 'WA', 'WV', 'WI', 'WY',
];

export const YES_NO = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
];

export const YEARS_AT_ADDR = ['<1', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10+'];
export const CDL_CLASSES = ['A', 'B', 'C'];

// The individual CDL endorsement codes (49 CFR §383.153).  'X' (the combined
// tank + hazmat code) is intentionally omitted: a driver who holds both just
// checks H and N, so listing X too would double-count the same qualification.
export const ENDORSEMENTS = [
  { code: 'H', label: 'Hazmat' },
  { code: 'N', label: 'Tank' },
  { code: 'T', label: 'Doubles / Triples' },
  { code: 'P', label: 'Passenger' },
  { code: 'S', label: 'School Bus' },
];

export const YEARS_CDL = ['1', '2', '3', '4', '5', '6-9', '10-14', '15-19', '20+'];

// Physical trailer / equipment types the driver has operated.  Deliberately
// NOT "Hazmat" — that's a cargo class, captured by the H endorsement + the
// Step 3 Hazmat-clearance field, so it doesn't belong in an equipment list.
export const EQUIPMENT_TYPES = [
  'Dry van', 'Reefer', 'Flatbed', 'Step deck', 'Tanker', 'Hopper',
  'Auto hauler', 'Doubles', 'Triples', 'Heavy haul / oversize',
  'Intermodal / container',
];
export const REGIONS = [
  'OTR (48-state)', 'Regional NE', 'Regional SE', 'Regional MW',
  'Regional SW', 'Regional W', 'Local / home daily', 'Canada', 'Mexico',
];
export const PREFERRED_ROLE = [
  { value: 'otr', label: 'OTR' },
  { value: 'regional', label: 'Regional' },
  { value: 'dedicated', label: 'Dedicated' },
  { value: 'local', label: 'Local' },
];

export const ACCIDENT_TYPES = ['Rear-end', 'Sideswipe', 'Roll-over', 'Jackknife', 'Fixed object', 'Animal', 'Other'];
export const INJURY_LEVELS = ['None', 'Injury', 'Fatality'];
export const PREVENTABLE = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
  { value: 'unk', label: 'Undetermined' },
];
export const CONVICTION_STATUS = ['Convicted', 'Dismissed', 'Pending', 'Reduced'];

export const CONTACT_OK = [
  { value: 'yes', label: 'Yes' },
  { value: 'later', label: 'After interview' },
  { value: 'no', label: 'No' },
];

export const HEARD_SOURCES = ['Indeed', 'Google Search', 'Facebook', 'TikTok', 'Driver Referral', 'LinkedIn', 'Other'];

// Disclosure text version — sent to the server so the certified copy is
// auditable.  Bump when the legal text below (Step 8) changes.
export const DISCLOSURE_VERSION = '2026-06-22';

// ── blank repeater items ────────────────────────────────────────────
export const blankJob = (): Data => ({
  company: '', phone: '', city: '', state: '', position: 'Driver',
  from: '', to: '', equipment: '', reason: '', gapExplanation: '',
  fmcsa: null, contactOk: null, current: false,
});
export const blankAddress = (): Data => ({ addr1: '', city: '', state: '', zip: '', from: '', to: '' });
export const blankAccident = (): Data => ({ date: '', location: '', type: '', injuries: '', preventable: null, desc: '' });
export const blankViolation = (): Data => ({ date: '', state: '', charge: '', penalty: '', status: '' });

export const todayISO = (): string => new Date().toISOString().slice(0, 10);
