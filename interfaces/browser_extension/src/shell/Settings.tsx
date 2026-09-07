import { useEffect, useState } from 'react';
import { getFollowPref, setFollowPref } from '../features/live-map/googleMaps';
import { getOverlayPref, setOverlayPref } from '../features/maps-overlay/pref';

/**
 * The panel's settings — every preference the panel keeps, in one
 * place.  A feature may also show its own quick toggle where the
 * choice is made (the Live Map's "Follow in Google Maps" chip); both
 * read and write the same stored preference.
 */
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
        <label className="row" style={{ justifyContent: 'space-between', cursor: 'pointer' }}>
          <span>
            Follow in Google Maps
            <span className="muted" style={{ display: 'block', fontSize: 12 }}>
              With Google Maps in front, selecting a vehicle moves Google's pin to it.
            </span>
          </span>
          <input type="checkbox" checked={!!follow} disabled={follow === null}
                 onChange={(e) => { setFollow(e.target.checked); void setFollowPref(e.target.checked); }} />
        </label>
      </section>

      <section style={{ display: 'grid', gap: 8 }}>
        <span className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>On google.com/maps</span>
        <label className="row" style={{ justifyContent: 'space-between', cursor: 'pointer' }}>
          <span>
            Show my vehicles on Google Maps
            <span className="muted" style={{ display: 'block', fontSize: 12 }}>
              Draws them on google.com/maps itself, so a route and your vehicles are one
              picture. The same switch sits on the map, top right, for turning them off
              where you see them.
            </span>
          </span>
          <input type="checkbox" checked={!!overlay} disabled={overlay === null}
                 onChange={(e) => { setOverlay(e.target.checked); void setOverlayPref(e.target.checked); }} />
        </label>
      </section>

      <section style={{ display: 'grid', gap: 4 }}>
        <span className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '.04em' }}>About</span>
        <span className="muted" style={{ fontSize: 12 }}>4truck for Chrome {manifest.version}</span>
        <span className="muted" style={{ fontSize: 12, fontFamily: 'ui-monospace, monospace' }}>{chrome.runtime.id}</span>
      </section>
    </div>
  );
}
