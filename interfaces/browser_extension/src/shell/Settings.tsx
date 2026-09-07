import { useEffect, useRef, useState } from 'react';
import { getFollowPref, setFollowPref } from '../features/live-map/googleMaps';
import { getOverlayPref, setOverlayPref } from '../features/maps-overlay/pref';

/**
 * The panel's settings — every preference the panel keeps, in one
 * place.  A feature may also show its own quick toggle where the
 * choice is made (the Live Map's "Follow in Google Maps" chip); both
 * read and write the same stored preference.
 */
/**
 * A switch whose value has not arrived yet is UNKNOWN, not off.
 *
 * Rendering `checked={!!value}` while the read is in flight shows a
 * definite "off" for a preference that may well be on — a person who
 * opens Settings to check whether following is enabled is told the
 * wrong answer for as long as the read takes.  `indeterminate` is the
 * state the platform already has for exactly this.
 */
function Toggle({ value, onChange, label, hint }: {
  value: boolean | null;
  onChange: (on: boolean) => void;
  label: string;
  hint: string;
}) {
  const box = useRef<HTMLInputElement>(null);
  useEffect(() => { if (box.current) box.current.indeterminate = value === null; }, [value]);
  return (
    <label className="row" style={{ justifyContent: 'space-between', cursor: value === null ? 'default' : 'pointer',
                                    opacity: value === null ? 0.6 : 1 }}>
      <span>
        {label}
        <span className="muted" style={{ display: 'block', fontSize: 12 }}>{hint}</span>
      </span>
      <input ref={box} type="checkbox" checked={value === true} disabled={value === null}
             aria-busy={value === null}
             onChange={(e) => onChange(e.target.checked)} />
    </label>
  );
}

export default function Settings({ onBack }: { onBack: () => void }) {
  const [follow, setFollow] = useState<boolean | null>(null);
  const [overlay, setOverlay] = useState<boolean | null>(null);
  useEffect(() => { void getFollowPref().then(setFollow); }, []);
  useEffect(() => { void getOverlayPref().then(setOverlay); }, []);

  const manifest = chrome.runtime.getManifest();

  return (
    <div style={{ padding: 12, display: 'grid', gap: 14 }}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <strong>Settings</strong>
        <button type="button" className="btn" onClick={onBack}>Back</button>
      </div>

      <section style={{ display: 'grid', gap: 8 }}>
        <span className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>In this panel</span>
        <Toggle value={follow} label="Follow in Google Maps"
                hint="With Google Maps in front, selecting a vehicle replaces what is open in that tab."
                onChange={(on) => { setFollow(on); void setFollowPref(on); }} />
      </section>

      <section style={{ display: 'grid', gap: 8 }}>
        <span className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>On google.com/maps</span>
        <Toggle value={overlay} label="Show my vehicles on Google Maps"
                hint="Draws them on google.com/maps itself, so a route and your vehicles are one picture. The same switch sits on the map, top right."
                onChange={(on) => { setOverlay(on); void setOverlayPref(on); }} />
      </section>

      <section style={{ display: 'grid', gap: 4 }}>
        <span className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>About</span>
        <span className="muted" style={{ fontSize: 12 }}>4truck for Chrome {manifest.version}</span>
        <span className="muted" style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace' }}>{chrome.runtime.id}</span>
      </section>
    </div>
  );
}
