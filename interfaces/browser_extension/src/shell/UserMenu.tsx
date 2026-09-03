import { useEffect, useRef, useState } from 'react';
import { initialsOf } from './initials';

export interface Me { name?: string | null; role?: string | null; account_name?: string | null }

/**
 * The avatar in the header and the menu under it: who is connected,
 * where the rest of 4truck is, the panel's settings, and Disconnect.
 * One place for everything that is about the person, not the feature —
 * the header stays the feature's.
 */
export default function UserMenu({ me, onSettings, onDisconnect }: {
  me: Me | null;
  onSettings: () => void;
  onDisconnect: () => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (!root.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', away);
    document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', away); document.removeEventListener('keydown', esc); };
  }, [open]);

  const label = me?.name || 'Connected';
  const pick = (fn: () => void) => () => { setOpen(false); fn(); };

  return (
    <div ref={root} style={{ position: 'relative' }}>
      <button type="button" className="avatar" aria-label="Account menu" aria-haspopup="menu" aria-expanded={open}
              title={label} onClick={() => setOpen((o) => !o)}>
        {initialsOf(me?.name)}
      </button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-head">
            <strong style={{ display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</strong>
            {(me?.account_name || me?.role) && (
              <span className="muted" style={{ fontSize: 12 }}>
                {[me?.account_name, me?.role].filter(Boolean).join(' · ')}
              </span>
            )}
          </div>
          <button type="button" role="menuitem" className="menu-item"
                  onClick={pick(() => { void chrome.tabs.create({ url: 'https://dash.4truck.us/' }); })}>
            Open 4truck
          </button>
          <button type="button" role="menuitem" className="menu-item" onClick={pick(onSettings)}>
            Settings
          </button>
          <div className="menu-sep" />
          <button type="button" role="menuitem" className="menu-item danger" onClick={pick(onDisconnect)}>
            Disconnect
          </button>
        </div>
      )}
    </div>
  );
}
