/**
 * A labelled field, for the two places this panel takes input: adding an
 * item and correcting one.
 *
 * Each field keeps a LABEL rather than leaning on its placeholder.
 * Three identical boxes told apart only by placeholder stop telling
 * anything apart the moment one is filled — the placeholder goes, and a
 * person who paused cannot re-read what they answered.
 */
import type { ReactNode } from 'react';

export default function Field({ label, required, children }: {
  label: string; required?: boolean; children: ReactNode;
}) {
  return (
    <label style={{ display: 'grid', gap: 3 }}>
      <span className="muted" style={{ fontSize: 11 }}>
        {/* Muted, not --warn: amber means "flagged, wants attention"
            everywhere else on this panel, and a required marker is not
            that.  An asterisk carries its meaning in its shape. */}
        {label}{required && <span style={{ color: 'var(--muted)' }}> *</span>}
      </span>
      {children}
    </label>
  );
}
