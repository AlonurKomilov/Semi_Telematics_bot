/**
 * Inventory's own configuration, reached from the gear beside the
 * feature's name.
 *
 * Two halves with two different blast radii, and this panel writes one.
 *
 * The CATALOGUE — what every vehicle of a type is expected to carry — is
 * read-only here.  Changing it decides whether a hundred trucks are
 * reported short, which is a desk decision; the flag that carries it is
 * deliberately not in a browser key's scope.  It is still SHOWN, because
 * somebody standing at a truck being told "one short" deserves to see
 * what the list is.
 *
 * The FOCUS — which of those categories THIS role goes red about — is
 * the narrow half.  It changes what one role is shown, never what a
 * truck is short, and it is exactly what a person at a truck wants to
 * turn down: the owner's words were that something going red for one
 * role pulls another role's attention onto what is not theirs.
 *
 * Config, not Settings.  Settings (the user menu) holds what this PANEL
 * does — follow a Google Maps tab, draw on the map.  This holds what the
 * FEATURE means for the account.
 */
import { useEffect, useState } from 'react';

import { apiJSON } from '../../api/client';
import { humanize } from './data';
import type { PanelFeatureProps } from '../../shell/registry';

interface CatalogueRow {
  category: string;
  label: string;
  quantity: number;
  required: boolean;
}

interface ConfigResponse {
  catalogue: Record<string, CatalogueRow[]>;
  vehicle_types: string[];
  role: string;
  /** `null` means this role has never narrowed — flagged on everything.
   *  `[]` means it asked to be flagged on nothing.  Two answers. */
  focus: string[] | null;
  can_edit_catalogue: boolean;
}

export default function InventoryConfig({ abilities }: PanelFeatureProps) {
  const [data, setData] = useState<ConfigResponse | null>(null);
  const [error, setError] = useState('');
  const [focus, setFocus] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let stopped = false;
    void apiJSON<ConfigResponse>('/extension/inventory-config')
      .then((d) => { if (!stopped) { setData(d); setFocus(d.focus); } })
      .catch((e: unknown) => {
        if (!stopped) setError(e instanceof Error ? e.message : 'Could not load the configuration');
      });
    return () => { stopped = true; };
  }, []);

  if (error) {
    return <p style={{ padding: 12, margin: 0, color: 'var(--danger)' }}>{error}</p>;
  }
  if (!data) {
    return <p className="muted" style={{ padding: 12, margin: 0 }}>Loading…</p>;
  }

  // Every category the account expects anywhere — the focus is about
  // categories, and a role may want to turn down a trailer's as readily
  // as a truck's.
  const all = data.vehicle_types
    .flatMap((t) => data.catalogue[t] ?? [])
    .map((r) => r.category)
    .filter((c, i, a) => a.indexOf(c) === i);
  // Never-narrowed is flagged on everything, so an untouched role shows
  // every box ticked rather than an empty list it never asked for.
  const ticked = focus ?? all;
  // The server says whether the flag is held; the panel does not guess
  // from a role name.
  const mayAim = abilities.includes('config.role');
  const dirty = JSON.stringify(ticked) !== JSON.stringify(data.focus ?? all);

  const toggle = (c: string) => {
    setSaved(false);
    setFocus(ticked.includes(c) ? ticked.filter((x) => x !== c) : [...ticked, c]);
  };

  const save = () => {
    setSaving(true); setError('');
    void apiJSON<{ categories: string[] }>('/extension/inventory-focus', {
      method: 'PUT', body: { categories: ticked },
    })
      .then((r) => { setData({ ...data, focus: r.categories }); setFocus(r.categories); setSaved(true); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not save'))
      .finally(() => setSaving(false));
  };

  return (
    <div style={{ padding: 12, display: 'grid', gap: 14 }}>
      <section style={{ display: 'grid', gap: 8 }}>
        <span className="muted eyebrow">What my role is flagged on</span>
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          Every role reads the same list below — this only decides which of it
          turns red on <strong>your</strong> screens. A truck still owes what it
          owes, and the roles that kept a category still see it.
        </p>
        {all.length === 0 ? (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            Nothing is expected on any vehicle yet, so there is nothing to
            turn down.
          </p>
        ) : (
          <div style={{ display: 'grid', gap: 4 }}>
            {all.map((c) => (
              <label key={c} className="row" style={{ gap: 6, minHeight: 24, cursor: mayAim ? 'pointer' : 'default' }}>
                <input type="checkbox" checked={ticked.includes(c)} disabled={!mayAim}
                       onChange={() => toggle(c)} style={{ width: 16, height: 16, flexShrink: 0 }} />
                <span style={{ fontSize: 12 }}>{humanize(c)}</span>
              </label>
            ))}
          </div>
        )}
        {mayAim ? (
          dirty && (
            <div className="row" style={{ gap: 6 }}>
              <button className="btn primary" disabled={saving}
                      title="Save what your role is flagged on"
                      onClick={save}>{saving ? 'Saving…' : 'Save'}</button>
              <button className="btn" disabled={saving}
                      onClick={() => { setFocus(data.focus); setSaved(false); }}>Discard</button>
            </div>
          )
        ) : (
          // Disabled WITH A REASON — a dead control that will not say what
          // it is waiting for is a dead end.
          <p className="muted" style={{ margin: 0, fontSize: 11 }}>
            Your account has not given this sign-in the right to re-aim your
            role's attention.
          </p>
        )}
        {saved && !dirty && (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            Saved. Your screens stop flagging what you unticked.
          </p>
        )}
      </section>

      {data.vehicle_types.map((t) => (
        <section key={t} style={{ display: 'grid', gap: 8 }}>
          <span className="muted eyebrow">Expected on every {t}</span>
          {(data.catalogue[t] ?? []).length === 0 ? (
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              Nothing is expected, so no {t} is ever reported short.
            </p>
          ) : (
            <div style={{ display: 'grid', gap: 2 }}>
              {(data.catalogue[t] ?? []).map((r) => (
                <div key={r.category} className="row" style={{ gap: 6, minHeight: 24, fontSize: 12 }}>
                  <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                        title={r.label || humanize(r.category)}>
                    {r.label || humanize(r.category)}
                  </span>
                  {r.quantity > 1 && <span className="muted" style={{ flexShrink: 0 }}>×{r.quantity}</span>}
                  {!r.required && (
                    <span className="muted" style={{ marginLeft: 'auto', flexShrink: 0, fontSize: 11 }}>
                      optional
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      ))}

      {!data.can_edit_catalogue && (
        <p className="muted" style={{ margin: 0, fontSize: 11 }}>
          The list itself is changed on 4truck — it decides what every vehicle
          in the account owes, which is not a decision this panel makes.
        </p>
      )}
    </div>
  );
}
