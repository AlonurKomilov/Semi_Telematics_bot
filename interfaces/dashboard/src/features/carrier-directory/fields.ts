// Section field templates for a carrier profile.
//
// These are the DEFAULT label→value rows a recruiting manager (recruiter +
// is_manager) fills in; the actual values live per-carrier in the profile's
// `content` JSON.  Recruiters see them read-only.  A manager can leave any
// field blank or add custom rows
// — the template just seeds the standard recruiting-playbook fields so every
// carrier is captured consistently.

export interface FieldRow { label: string; value: string }

export interface Section {
  key: string;
  title: string;
  kind: 'text' | 'rows';
  /** Default field labels for a 'rows' section. */
  fields?: string[];
}

export const SECTIONS: Section[] = [
  {
    key: 'application_process',
    title: 'Application Process',
    kind: 'text',
  },
  {
    key: 'prequal',
    title: 'Pre-Qualifications',
    kind: 'rows',
    fields: [
      'Minimum Age',
      'Minimum Experience (Tractor-Trailer / OTR)',
      'Moving Violations (max in 3-year period)',
      'Policy on License Suspensions',
      'DOT Recordable Accidents',
      'Maximum Major Moving Violation (last 3 years)',
      'Max. Number of Jobs (last 3 years)',
      'Policy Against Unemployment',
      'Terminated Applicants',
      'Criminal Convictions',
      'DUI / DWI',
      'Is Haz-Mat Required?',
      'Other Endorsements Required',
      'DOT Physical Requirements',
      'Long-Form Physical Required Up Front?',
      'Drug Testing',
      'Other Automatic DQs',
    ],
  },
  {
    key: 'presentation',
    title: 'Presentation',
    kind: 'rows',
    fields: [
      'Sign-On Bonus', 'Driver Types', 'Types of Runs', 'Type of Freight',
      'Type of Equipment', 'Cameras', 'Transmission Type', 'Average Age of Tractor',
      'Is Truck Permanently Assigned?', 'Truck Speed', 'Can Truck Be Taken Home?',
      'Inverters / APUs', '% Drop and Hook', '% No Touch', '% Haz-Mat Loads',
      'Pay Scale (Solo)', 'Type of Driver Pay', 'When Are Drivers Paid?',
      'How Are Drivers Paid?', 'Pay Increase', 'Hiring Areas', 'Primary Running Areas',
      'Average Miles per Week', 'Average Length of Haul', 'Home Time / Days Out',
      'Average Weekly Pay', 'Driver Vacation Info', 'EZ Pass Provided', 'Pre-Pass Provided',
      'Toll Cards Provided', 'Type of Fuel Card', 'Breakdown Pay', 'Layover Pay',
      'Dock Detention Pay', 'Multi-Stop Pay', 'New York City', 'Safety Bonus',
      'Rider Policy', 'Pet Policy', '24-Hour Dispatch?', 'Routing / Fuel-Stop Flexibility',
      'Qualcomm Provided', 'Is Per Diem Optional?', 'Paid Orientation',
      'How Long Is Orientation?', 'Orientation Held Where?', 'Orientation Start / End Days',
      'Lodging Provided', 'Meals Provided', 'Travel Provided', 'Insurance Starts When?',
      'Life Insurance', '401(k) Retirement Plan',
    ],
  },
  {
    key: 'recruiter_only',
    title: 'For Recruiters Only',
    kind: 'rows',
    fields: [
      'Application Turnaround Time',
      'Rehire Policy',
      'Application Ownership',
    ],
  },
];

export type CarrierContent = {
  application_process?: string;
  prequal?: FieldRow[];
  presentation?: FieldRow[];
  recruiter_only?: FieldRow[];
  [key: string]: string | FieldRow[] | undefined;
};

/** Merge a section's stored rows over its template so every default label
 *  shows (value or blank) and any manager-added custom rows are preserved. */
export function mergeRows(template: string[] = [], stored: FieldRow[] = []): FieldRow[] {
  const byLabel = new Map(stored.map((r) => [r.label, r.value]));
  const templated = template.map((label) => ({ label, value: byLabel.get(label) ?? '' }));
  const custom = stored.filter((r) => !template.includes(r.label));
  return [...templated, ...custom];
}
