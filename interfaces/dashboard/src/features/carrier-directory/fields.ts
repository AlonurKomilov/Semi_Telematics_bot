// Section field templates for a carrier profile.
//
// These are the DEFAULT label→value rows a recruiting manager (recruiter +
// is_manager) fills in; the actual values live per-carrier in the profile's
// `content` JSON.  Recruiters see them read-only.  A manager can leave any
// field blank or add custom rows — the template just seeds the standard
// recruiting-playbook fields so every carrier is captured consistently.
//
// SECOND AUDIENCE: since the self-fill invite shipped, these exact strings are
// also read by an OUTSIDE carrier's office manager with no recruiting jargon
// and no glossary.  Every label here has to be answerable by that reader, not
// just recognisable to us.  That is why fields carry `hint` and `type`.
//
// RENAMING A LABEL IS A DATA OPERATION.  Stored rows are matched to the
// template BY LABEL, so a rename orphans the stored value unless the old
// string is listed in `renamedFrom` — mergeRows adopts it and re-files the
// value under the new label on the next save.  Never rename without it.

export interface FieldRow { label: string; value: string }

/** How a value should be captured.  Everything defaults to a one-line text
 *  input; the others exist because a bare text box invites "yes" as the
 *  answer to a field that wanted a brand name or a number. */
export type FieldType = 'text' | 'bool' | 'count' | 'pct' | 'money' | 'longtext';

export interface FieldDef {
  label: string;
  /** Previous label(s) this field shipped under.  Keeps stored values
   *  attached across a rename — see the header note. */
  renamedFrom?: string[];
  type?: FieldType;
  /** A real example for THIS field, shown as the input placeholder.
   *  Type-derived examples were nonsense at field level — "e.g. 2" landed
   *  on Minimum Age, Average Miles per Week and Governed Truck Speed
   *  alike. Omit rather than invent one. */
  example?: string;
  /** Sub-heading this field sits under, for sections too long to scan. */
  group?: string;
  /** One line of plain-language clarification, shown under the label. */
  hint?: string;
}

export interface Section {
  key: string;
  title: string;
  kind: 'text' | 'rows';
  /** Shown under the section title on both the editor and the public form. */
  blurb?: string;
  /** True for sections an external carrier must never see or write. The
   *  backend enforces this independently (_INTAKE_ROW_SECTIONS in
   *  features/carrier_directory/router.py); this flag only makes the wall
   *  VISIBLE to the manager relying on it. */
  internal?: boolean;
  fields?: FieldDef[];
}

const f = (
  label: string,
  extra: Omit<FieldDef, 'label'> = {},
): FieldDef => ({ label, ...extra });

// Section order is deliberate and carrier-first: the sections a carrier WANTS
// to talk about (what they pay, what they drive) come before the legally
// loaded screening block, and the free-text essay comes last.  Leading with
// criminal-conviction policy is how you get an abandoned form.
export const SECTIONS: Section[] = [
  {
    key: 'presentation',
    title: 'What Drivers Get',
    kind: 'rows',
    blurb: 'Pay, routes and home time, equipment, benefits and orientation \u2014 what recruiters tell drivers about you. The more of this you fill in, the more accurately you are presented.',
    fields: [
      // ── Pay ──────────────────────────────────────────────────────
      // The five pay labels used to be mutually indistinguishable
      // ("Type of Driver Pay" vs "How Are Drivers Paid?"); each now names
      // exactly one dimension and says so in its hint.
      f('Solo Pay Rate', { example: '$0.58/mile', group: 'Pay', renamedFrom: ['Pay Scale (Solo)'], hint: 'e.g. $0.58/mile, or 27% of line haul' }),
      f('Pay Structure', { group: 'Pay', renamedFrom: ['Type of Driver Pay'], hint: 'Cents per mile, percentage, hourly, or salary' }),
      f('Payment Method', { group: 'Pay', renamedFrom: ['How Are Drivers Paid?'], hint: 'Direct deposit, pay card, or cheque' }),
      f('Pay Frequency', { group: 'Pay', renamedFrom: ['When Are Drivers Paid?'], hint: 'Weekly, bi-weekly — and which day' }),
      f('Average Weekly Pay', { example: '$1,400', group: 'Pay', type: 'money' }),
      f('Pay Increase', { group: 'Pay', hint: 'How and when raises happen' }),
      f('Sign-On Bonus', { example: '$2,000', group: 'Pay', type: 'money' }),
      f('Safety Bonus', { group: 'Pay' }),
      f('Breakdown Pay', { group: 'Pay' }),
      f('Layover Pay', { group: 'Pay' }),
      f('Dock Detention Pay', { group: 'Pay' }),
      f('Multi-Stop Pay', { group: 'Pay' }),
      f('Per Diem Optional', { renamedFrom: ['Is Per Diem Optional?'], group: 'Pay', type: 'bool' }),

      // ── Routes & home time ───────────────────────────────────────
      f('Driver Types', { group: 'Routes & Home Time', hint: 'Solo, team, owner-operator' }),
      f('Types of Runs', { group: 'Routes & Home Time', hint: 'OTR, regional, dedicated, local' }),
      f('Type of Freight', { group: 'Routes & Home Time' }),
      f('Home Time / Days Out', { example: '14 days out, 2 home', group: 'Routes & Home Time' }),
      f('Average Miles per Week', { example: '2,800', group: 'Routes & Home Time', type: 'count' }),
      f('Average Length of Haul', { example: '900 miles', group: 'Routes & Home Time' }),
      f('Hiring Areas', { group: 'Routes & Home Time', hint: 'States or regions you hire drivers from' }),
      f('Primary Running Areas', { group: 'Routes & Home Time', hint: 'Where the trucks actually run' }),
      f('% Drop and Hook', { example: '80%', group: 'Routes & Home Time', type: 'pct' }),
      f('% No Touch', { example: '95%', group: 'Routes & Home Time', type: 'pct' }),
      f('% Haz-Mat Loads', { example: '10%', group: 'Routes & Home Time', type: 'pct' }),
      // Was the bare place name "New York City", which is not a question.
      f('NYC Runs Required', { group: 'Routes & Home Time', type: 'bool', renamedFrom: ['NYC Runs Required?', 'New York City'], hint: 'Do drivers have to run into New York City?' }),

      // ── Equipment ────────────────────────────────────────────────
      f('Type of Equipment', { group: 'Equipment' }),
      f('Average Age of Tractor', { example: '2 years', group: 'Equipment' }),
      f('Transmission Type', { group: 'Equipment', hint: 'Automatic, manual, or both' }),
      f('Governed Truck Speed (mph)', { example: '68', group: 'Equipment', type: 'count', renamedFrom: ['Truck Speed'] }),
      f('Truck Permanently Assigned', { renamedFrom: ['Is Truck Permanently Assigned?'], group: 'Equipment', type: 'bool' }),
      f('Truck Can Be Taken Home', { renamedFrom: ['Can Truck Be Taken Home?'], group: 'Equipment', type: 'bool' }),
      // "Cameras" alone gets answered "yes"; drivers care which way they point.
      f('In-Cab Cameras', { group: 'Equipment', renamedFrom: ['Cameras'], hint: 'Road-facing only, or driver-facing too?' }),
      f('Inverters / APUs', { group: 'Equipment' }),
      // "Qualcomm" is a brand used as a category, and a dated one.
      f('ELD / In-Cab Unit', { group: 'Equipment', renamedFrom: ['Qualcomm Provided'], hint: 'Which system — e.g. Samsara, Motive, Qualcomm' }),
      f('EZ Pass Provided', { group: 'Equipment', type: 'bool' }),
      f('Pre-Pass Provided', { group: 'Equipment', type: 'bool' }),
      f('Toll Cards Provided', { group: 'Equipment', type: 'bool' }),
      f('Type of Fuel Card', { group: 'Equipment' }),
      f('Routing / Fuel-Stop Flexibility', { group: 'Equipment' }),
      f('24-Hour Dispatch', { renamedFrom: ['24-Hour Dispatch?'], group: 'Equipment', type: 'bool' }),

      // ── Benefits ─────────────────────────────────────────────────
      f('Insurance Starts', { renamedFrom: ['Insurance Starts When?'], group: 'Benefits & Policies' }),
      f('Life Insurance', { group: 'Benefits & Policies' }),
      f('401(k) Retirement Plan', { group: 'Benefits & Policies' }),
      f('Driver Vacation Info', { group: 'Benefits & Policies' }),
      f('Rider Policy', { group: 'Benefits & Policies' }),
      f('Pet Policy', { group: 'Benefits & Policies' }),

      // ── Orientation ──────────────────────────────────────────────
      f('Paid Orientation', { group: 'Orientation', type: 'bool' }),
      f('Orientation Length', { example: '3 days', renamedFrom: ['How Long Is Orientation?'], group: 'Orientation' }),
      f('Orientation Location', { renamedFrom: ['Orientation Held Where?'], group: 'Orientation' }),
      f('Orientation Start / End Days', { group: 'Orientation' }),
      f('Lodging Provided', { group: 'Orientation', type: 'bool' }),
      f('Meals Provided', { group: 'Orientation', type: 'bool' }),
      f('Travel Provided', { group: 'Orientation', type: 'bool' }),
    ],
  },
  {
    key: 'prequal',
    title: 'Hiring Requirements',
    kind: 'rows',
    blurb: 'Who you will and will not hire. Recruiters screen against this before sending anyone your way.',
    fields: [
      f('Minimum Age', { example: '23', type: 'count' }),
      f('Minimum Experience (Tractor-Trailer / OTR)', { hint: 'The detailed rule — the one-line version goes in the summary field above' }),
      // These two were near-identical: a plural COUNT and a singular SEVERITY,
      // both "in 3 years", so neither could be answered confidently.
      f('Max Moving Violations (last 3 years)', { example: '2', type: 'count', renamedFrom: ['Moving Violations (max in 3-year period)'], hint: 'How many minor violations you tolerate' }),
      f('Max Serious Violations (last 3 years)', { example: '0', type: 'count', renamedFrom: ['Maximum Major Moving Violation (last 3 years)'], hint: 'Reckless, excessive speed, and similar majors' }),
      f('DOT Recordable Accidents', { hint: 'How many, and over what period' }),
      f('Max Jobs (last 3 years)', { example: '4', type: 'count', renamedFrom: ['Max. Number of Jobs (last 3 years)'], hint: 'Job-hopping limit, if you have one' }),
      // Read as a stance on unemployment BENEFITS; it means employment gaps.
      f('Employment Gaps — Policy', { renamedFrom: ['Policy Against Unemployment'], hint: 'How long a gap between jobs you accept, and whether it must be explained' }),
      // A bare noun with no question in it.
      f('Previously Terminated Drivers', { renamedFrom: ['Do You Consider Previously Terminated Drivers?', 'Terminated Applicants'] }),
      f('Policy on License Suspensions', {}),
      f('Criminal Convictions', { hint: 'What disqualifies, and any look-back window' }),
      f('DUI / DWI', { hint: 'Ever, or within a window?' }),
      f('Haz-Mat Required', { renamedFrom: ['Is Haz-Mat Required?'], type: 'bool' }),
      f('Other Endorsements Required', {}),
      f('DOT Physical Requirements', {}),
      f('Long-Form Physical Required Up Front', { renamedFrom: ['Long-Form Physical Required Up Front?'], type: 'bool' }),
      f('Drug Testing', { hint: 'Type and when — pre-hire, random, hair vs urine' }),
      f('Other Automatic Disqualifiers', { renamedFrom: ['Other Automatic DQs'], type: 'longtext' }),
    ],
  },
  {
    key: 'application_process',
    title: 'How to Submit Drivers',
    kind: 'text',
    blurb: 'Where recruiters send applications, who to contact, and what happens next.',
  },
  {
    key: 'recruiter_only',
    title: 'Internal — Never Shown to the Carrier',
    kind: 'rows',
    internal: true,
    blurb: 'Our own notes on working with this carrier. The carrier cannot see or edit this section, even through their fill-in link.',
    fields: [
      f('Application Turnaround Time', {}),
      f('Rehire Policy', {}),
      f('Application Ownership', { hint: 'Who owns the candidate, and for how long' }),
    ],
  },
];

/** Sections an external carrier fills in — the mirror of the backend's
 *  _INTAKE_TEXT_SECTIONS / _INTAKE_ROW_SECTIONS allow-lists. */
export const PUBLIC_SECTIONS = SECTIONS.filter((s) => !s.internal);

export type CarrierContent = {
  application_process?: string;
  prequal?: FieldRow[];
  presentation?: FieldRow[];
  recruiter_only?: FieldRow[];
  [key: string]: string | FieldRow[] | undefined;
};

/** Merge a section's stored rows over its template so every default label
 *  shows (value or blank) and any manager-added custom rows are preserved.
 *
 *  Two things this has to get right:
 *  - RENAMES: a value stored under an old label is adopted by the field that
 *    lists it in `renamedFrom`, so the value survives and re-files itself on
 *    the next save instead of stranding at the bottom as a custom row.
 *  - DUPLICATES: two stored rows sharing a label used to collapse silently
 *    (last one won). Only the first is adopted into the template slot now;
 *    the rest survive as custom rows so no typed value is ever dropped. */
export function mergeRows(
  template: FieldDef[] = [],
  stored: FieldRow[] = [],
): FieldRow[] {
  const byLabel = new Map<string, string>();
  for (const r of stored) {
    if (!byLabel.has(r.label)) byLabel.set(r.label, r.value);
  }
  const claimed = new Set<string>();
  const templated = template.map((def) => {
    const names = [def.label, ...(def.renamedFrom ?? [])];
    const hit = names.find((n) => byLabel.has(n) && (byLabel.get(n) ?? '') !== '');
    const key = hit ?? (names.find((n) => byLabel.has(n)) ?? def.label);
    if (byLabel.has(key)) claimed.add(key);
    // Also claim any OTHER name of this field that is stored but blank.
    // Without this, "current label stored blank + alias holds the value"
    // leaves the blank row unclaimed, and it reappears in the custom tail
    // as a second row wearing the same visible label. Blank rows carry no
    // data, so absorbing them loses nothing — whereas a stale NON-blank
    // value under an old label is left alone deliberately, surfacing as a
    // custom row a human can read and delete.
    for (const n of names) {
      if (n !== key && byLabel.has(n) && (byLabel.get(n) ?? '') === '') claimed.add(n);
    }
    return { label: def.label, value: byLabel.get(key) ?? '' };
  });
  // Anything the template didn't claim — genuine custom rows, plus any
  // duplicate-label leftovers — keeps its place at the end.
  const seen = new Map<string, number>();
  const custom = stored.filter((r) => {
    const n = (seen.get(r.label) ?? 0) + 1;
    seen.set(r.label, n);
    return !(claimed.has(r.label) && n === 1);
  });
  return [...templated, ...custom];
}

/** Safe href for a URL a user typed — including an UNAUTHENTICATED external
 *  carrier, who can write `website` and `video_url` through the public intake
 *  endpoint.  Returns '' for anything carrying a non-http(s) scheme, so a
 *  `javascript:` value can never reach an <a href> in the dashboard.
 *  Callers must render nothing when this returns ''. */
export function safeHref(raw: string | undefined | null): string {
  const t = (raw ?? '').trim();
  if (!t) return '';
  if (/^https?:\/\//i.test(t)) return t;
  // Any other explicit scheme (javascript:, data:, vbscript:, file:…) is
  // refused outright rather than coerced — prefixing https:// onto
  // "javascript:alert(1)" would only produce a broken link.
  //
  // A control-character-split payload ("java\tscript:alert(1)") slips past
  // this regex, and that is fine BY CONSTRUCTION: the fallback below
  // prepends https://, so it becomes an unambiguous https URL rather than
  // a script. The allow-list shape — recognise http(s), else prefix — is
  // what makes the check safe, not the completeness of the deny regex.
  if (/^[a-z][a-z0-9+.-]*:/i.test(t)) return '';
  return `https://${t}`;
}

/** Read one field's stored value off a carrier's content blob.
 *
 *  Matches BY LABEL, walking `renamedFrom` the same way mergeRows does —
 *  otherwise a grid column would read blank for every carrier who answered
 *  before the label was last renamed, which looks exactly like "they never
 *  told us" and is the worst possible lie for a screening view.
 *  Prefers a non-empty answer over an empty one under any of the names. */
export function valueOf(
  content: CarrierContent | undefined,
  sectionKey: string,
  def: FieldDef,
): string {
  const rows = (content?.[sectionKey] as FieldRow[] | undefined) ?? [];
  if (!Array.isArray(rows)) return '';
  const names = [def.label, ...(def.renamedFrom ?? [])];
  for (const n of names) {
    const hit = rows.find((r) => r?.label === n && String(r.value ?? '').trim());
    if (hit) return String(hit.value).trim();
  }
  return '';
}

/** Every carrier-visible field, flattened with the section it belongs to —
 *  the source for the directory grid's optional columns. */
export const FIELD_COLUMNS: { key: string; def: FieldDef; section: Section }[] =
  SECTIONS.flatMap((section) =>
    (section.fields ?? []).map((def) => ({
      // Stable, collision-proof key. Deriving it from the LABEL would
      // change the operator's saved column choices on the next rename.
      key: `f:${section.key}:${(def.renamedFrom?.[def.renamedFrom.length - 1] ?? def.label)
        .toLowerCase().replace(/[^a-z0-9]+/g, '_')}`,
      def,
      section,
    })),
  );
