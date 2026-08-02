// One value control for a carrier-profile field, chosen by the field's
// declared type.  Shared by the manager editor (CarrierProfile) and the
// public self-fill form (PublicCarrierIntake) so a given field is captured
// the same way on both sides — a field answered "yes" by a carrier and
// "Yes — road-facing" by a recruiter is the drift this prevents.
//
// Why this exists at all: every field used to be a bare text input with a
// placeholder of "—", so a boolean, a percentage, a currency amount and a
// paragraph all wore one shape. "In-Cab Cameras" reliably came back as
// "yes", which answers a question nobody asked.
import { Input } from '../../components/ui/input';
import { Textarea } from '../../components/ui/textarea';
import type { FieldDef } from './fields';

const BOOL_OPTIONS = ['Yes', 'No'];

interface Props {
  def?: FieldDef;
  value: string;
  onChange: (v: string) => void;
  /** Accessible name, for the public form where the label is a sibling
   *  <span> rather than a wrapping <label>. */
  ariaLabel?: string;
}

export default function FieldValueInput({ def, value, onChange, ariaLabel }: Props) {
  const type = def?.type ?? 'text';
  const label = ariaLabel ?? def?.label;

  if (type === 'bool') {
    // A plain <select> rather than the design-system Select: this renders
    // up to ~15 times per section, and the native control is what a
    // carrier's office manager gets on a phone keyboard-free.
    return (
      <select
        aria-label={label}
        value={BOOL_OPTIONS.includes(value) ? value : ''}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-md border border-border bg-card px-3 text-sm text-foreground"
      >
        <option value="">—</option>
        {BOOL_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
        {/* A value typed before this field became a boolean stays
            selectable, so switching the type never silently drops data. */}
        {value && !BOOL_OPTIONS.includes(value) && <option value={value}>{value}</option>}
      </select>
    );
  }

  if (type === 'longtext') {
    return (
      <Textarea rows={3} aria-label={label} value={value}
        onChange={(e) => onChange(e.target.value)} />
    );
  }

  const numeric = type === 'count' || type === 'pct' || type === 'money';
  return (
    <Input
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      // inputMode, not type="number": these stay free text so "2 years" or
      // "$0.58/mile" remain sayable — the hint just gets the right keypad
      // up on a phone.
      inputMode={numeric ? 'decimal' : undefined}
      // A real example for THIS field, or nothing. Type-derived examples
      // read as nonsense at field level — one "e.g. 2" served Minimum Age,
      // Average Miles per Week and Governed Truck Speed alike. And "—" is
      // reserved for "no value" in READ mode, so it can never be a
      // placeholder: an empty input would look like a filled one.
      placeholder={def?.example ? `e.g. ${def.example}` : undefined}
    />
  );
}
