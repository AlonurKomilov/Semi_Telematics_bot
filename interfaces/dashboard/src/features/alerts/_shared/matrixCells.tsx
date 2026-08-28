/**
 * The two cells every notification matrix is built from.
 *
 * There are two of those matrices now — alert TYPES ("Engine Faults ×
 * Telegram") and a person's own TRIGGERS ("Fuel level below 30% ×
 * Telegram") — and they answer the same question about different rows.
 * They share this file rather than a copied pair of components, because a
 * grammar that exists twice is a grammar that drifts: the moment one grid
 * grows a disabled state or a hit-area fix the other doesn't, the page
 * reads as two features instead of one idea.
 *
 * ``hint`` and ``label`` are deliberately separate.  ``hint`` is the
 * REASON a column is greyed ("Connect and verify your email above first")
 * — it belongs on the header and, when disabled, on the cell.  ``label``
 * is the cell's accessible NAME, and it has to exist whether the cell is
 * enabled or not: a matrix cell has no visible text of its own, its row
 * label is a <td> rather than a <th>, and nothing here sets headers/scope
 * — so without an explicit name a screen reader announces eight identical
 * "checkbox, unchecked" and the grid is unusable.
 */
import type { LucideIcon } from 'lucide-react';

import { Checkbox } from '@/components/ui/checkbox';
import { Tip } from '@/components/tooltip';

export function MatrixTh({ icon: Icon, label, hint }: {
  icon: LucideIcon; label: string; hint: string;
}) {
  const head = (
    <span className={`inline-flex items-center gap-1.5 font-medium ${
      hint ? 'opacity-50' : ''
    }`}>
      <Icon className="size-3.5" aria-hidden /> {label}
    </span>
  );
  return (
    <th className="pb-2 px-2 text-center font-medium w-24">
      {hint ? <Tip label={hint}>{head}</Tip> : head}
    </th>
  );
}

export function MatrixCell({ checked, disabled, busy, hint, label, onChange }: {
  checked: boolean;
  /** Greyed because the CHANNEL can't deliver — a durable reason the
   *  person can act on, not a transient one. */
  disabled: boolean;
  /** A write for this cell is in flight.  Deliberately NOT folded into
   *  `disabled`: disabling the control a keyboard user just pressed Space
   *  on blurs it, and focus falls to <body> for the length of a request.
   *  `aria-busy` says the same thing without moving anybody. */
  busy?: boolean;
  /** Why this column is greyed — shown on hover when disabled. */
  hint: string;
  /** The cell's accessible name, e.g. "Telegram — Engine Faults". */
  label: string;
  onChange: (v: boolean) => void | Promise<void>;
}) {
  const box = (
    <Checkbox
      checked={checked}
      disabled={disabled}
      onChange={(e) => void onChange(e.target.checked)}
      aria-label={disabled && hint ? `${label} — ${hint}` : label}
      aria-busy={busy || undefined}
    />
  );
  return (
    <td className={`py-2.5 px-2 text-center ${busy ? 'opacity-60' : ''}`}>
      {/* Disabled inputs swallow pointer events, so the Tip needs a
          wrapper element to hover — without it the reason never shows. */}
      {disabled && hint ? <Tip label={hint}><span>{box}</span></Tip> : box}
    </td>
  );
}
