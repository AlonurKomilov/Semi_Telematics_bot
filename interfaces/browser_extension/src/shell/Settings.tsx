import { useEffect, useRef, useState } from 'react';
import { FOLLOW_KEY, FOLLOW_WARNING, getFollowPref, markFollowWarned, setFollowPref,
         wasFollowWarned } from '../prefs';
import { OVERLAY_PREF_KEY, getOverlayPref, setOverlayPref } from '../features/maps-overlay/pref';

/**
 * The panel's settings — every preference the panel keeps, and the ONLY
 * place they are changed.
 *
 * They were not always.  "Follow in Google Maps" was rendered here AND
 * in the Live Map AND in Inventory — three switches for one stored
 * value, each with its own copy of the first-time notice.  A preference
 * offered in every feature that happens to use it stops reading as a
 * property of the panel and starts reading as a property of the screen
 * you are on, and the person has to wonder whether the one in front of
 * them is the same switch.
 *
 * The rule now: a panel preference is changed HERE.  A feature READS it
 * and behaves accordingly; it does not offer a second door to it.
 *
 * The one exception, and the test that justifies it: a surface from
 * which this screen cannot be reached at all.  The overlay switch on
 * google.com/maps qualifies — somebody looking at the map is not
 * looking at the panel — so it stays. Nothing inside the panel does.
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
  const [followNotice, setFollowNotice] = useState('');
  useEffect(() => { void getFollowPref().then(setFollow); }, []);

  const onFollowChange = (on: boolean) => {
    setFollow(on);
    void setFollowPref(on);
    if (!on) { setFollowNotice(''); return; }
    // Said once, ever — it explains a consequence, and a consequence
    // repeated every time becomes something people stop reading.
    void wasFollowWarned().then((warned) => {
      if (warned) return;
      setFollowNotice(FOLLOW_WARNING);
      void markFollowWarned();
    });
  };
  useEffect(() => { void getOverlayPref().then(setOverlay); }, []);

  // …and KEEP them in step.  Both values have a second writer outside
  // this screen — the switch drawn on google.com/maps writes the overlay
  // pref, and a panel left open while somebody flips it showed the old
  // answer to the question it exists to answer.  This is the shape the
  // Live Map already uses; Settings was read-once.
  useEffect(() => {
    const onChange = (c: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area !== 'local') return;
      if (OVERLAY_PREF_KEY in c) setOverlay(c[OVERLAY_PREF_KEY].newValue !== false);
      if (FOLLOW_KEY in c) setFollow(c[FOLLOW_KEY].newValue === true);
    };
    chrome.storage.onChanged.addListener(onChange);
    return () => chrome.storage.onChanged.removeListener(onChange);
  }, []);

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
                onChange={onFollowChange} />
        {/* The first-time notice, now shown where the switch actually
            is.  It used to fire inside whichever panel you happened to
            toggle from, so the warning about replacing your tab lived
            two screens away from the setting that does it. */}
        {followNotice && <p className="muted" style={{ margin: 0, fontSize: 12 }}>{followNotice}</p>}
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
