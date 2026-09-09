/**
 * Which feature the panel is showing — the header title, made a choice.
 *
 * It appears ONLY when there is a choice to make.  An account with one
 * feature keeps the header it has always had: a title, not a control
 * that opens a menu of one.  A person whose grants reach only Live Map
 * never learns that a switch exists, which is correct — offering a door
 * and then refusing at it is worse than not showing the door.
 */
import { useEffect, useRef, useState } from 'react';
import type { PanelFeature } from './registry';

export default function FeatureMenu({ features, current, onPick }: {
  features: PanelFeature[];
  current: PanelFeature;
  onPick: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  // Same dismissal as the account menu beside it: a click away, or Escape.
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [open]);

  if (features.length < 2) {
    return <strong>4truck · {current.label}</strong>;
  }

  return (
    <div ref={root} style={{ position: 'relative', minWidth: 0 }}>
      {/* The title IS the control, so it carries the caret that says so.
          No border: a bordered button in the header would out-weigh the
          feature it names, and the header has exactly one other control
          (the avatar) to stay lighter than. */}
      <button type="button" onClick={() => setOpen((o) => !o)}
              aria-haspopup="menu" aria-expanded={open}
              title="Switch feature"
              className="row rowbtn"
              style={{ gap: 6, background: 'transparent', border: 0, padding: '2px 6px',
                       margin: '0 -6px', borderRadius: 4, minHeight: 24, minWidth: 0,
                       color: 'var(--fg)', cursor: 'pointer', font: 'inherit', textAlign: 'left' }}>
        <strong style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', minWidth: 0 }}>
          4truck · {current.label}
        </strong>
        {/* It does NOT flip.  Every fold in this panel reads open as
            ▾, so a caret that points up while a thing is open would
            make one glyph mean two opposite states within one header.
            A menu is not a disclosure; this is the select's caret,
            saying "there are others" and nothing about state. */}
        <span aria-hidden className="muted" style={{ flexShrink: 0 }}>▾</span>
      </button>
      {open && (
        <div className="menu" role="menu" style={{ left: 0, right: 'auto' }}>
          {features.map((f) => (
            <button key={f.id} type="button" role="menuitemradio" aria-checked={f.id === current.id}
                    className="menu-item"
                    onClick={() => { setOpen(false); onPick(f.id); }}>
              {/* A tick, not a highlight: the menu's items are a choice
                  already made, and the eye should find the current one
                  without reading every line. */}
              <span aria-hidden style={{ display: 'inline-block', width: 14 }}>
                {f.id === current.id ? '✓' : ''}
              </span>
              {f.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
