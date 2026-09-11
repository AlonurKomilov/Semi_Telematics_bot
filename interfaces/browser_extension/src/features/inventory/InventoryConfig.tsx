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
  /** The shipped default, so "start from the standard list" needs no
   *  second round trip — the same shape the dashboard is handed. */
  standard: Record<string, CatalogueRow[]>;
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
  /** Which type's list is shown.  The focus above spans both, because a
   *  role turns a CATEGORY down, not a category-on-a-trailer. */
  const [type, setType] = useState('truck');
  /** The catalogue being edited, or null while it follows the server.
   *  Reset when the type changes: an edit to trucks must not follow the
   *  reader over to trailers. */
  const [draft, setDraft] = useState<CatalogueRow[] | null>(null);
  const [savingRows, setSavingRows] = useState(false);
  const [rowsSaved, setRowsSaved] = useState(false);
  useEffect(() => { setDraft(null); }, [type]);

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
  // The catalogue half.  The SERVER answers it — the panel hides a
  // control the server would refuse rather than offering it and
  // collecting a 403 on the press.
  const mayEdit = data.can_edit_catalogue;
  const rows = draft ?? data.catalogue[type] ?? [];
  const rowsDirty = draft !== null;

  const editRow = (n: number, patch: Partial<CatalogueRow>) => {
    setRowsSaved(false);
    setDraft(rows.map((r, x) => (x === n ? { ...r, ...patch } : r)));
  };

  const saveRows = () => {
    setSavingRows(true); setError('');
    void apiJSON<{ catalogue: Record<string, CatalogueRow[]> }>(
      '/extension/inventory-catalogue',
      { method: 'PUT', body: { vehicle_type: type, items: rows } },
    )
      .then((r) => { setData({ ...data, catalogue: r.catalogue }); setDraft(null); setRowsSaved(true); })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : 'Could not save the list'))
      .finally(() => setSavingRows(false));
  };
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

  // Nothing declared ANYWHERE is one fact, and it was being reported four
  // times: once by the focus section, once by the line explaining that the
  // focus cannot be aimed, and once per vehicle type.  A screen of four
  // refusals reads as a broken feature rather than an unconfigured one.
  // An account that has declared nothing says so ONCE — it was saying it
  // four times, which reads as a broken feature rather than an
  // unconfigured one.  But the SHAPE stays: the type chips and the section
  // headings are how a person learns what this screen will hold, and a
  // panel that shows a bare sentence where the dashboard shows a
  // structure is two different products for one feature.
  const nothingDeclared = all.length === 0;

  return (
    <div style={{ padding: 12, display: 'grid', gap: 14 }}>
      {!nothingDeclared && (
      <section style={{ display: 'grid', gap: 8 }}>
        <span className="muted eyebrow">What my role is flagged on</span>
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>
          Every role reads the same list below — this only decides which of it
          turns red on <strong>your</strong> screens. A truck still owes what it
          owes, and the roles that kept a category still see it.
        </p>
        {/* `all` is non-empty here: the whole surface returns early when the
            account has declared nothing, so this branch cannot be reached. */}
        <div style={{ display: 'grid', gap: 4 }}>
          {all.map((c) => (
              <label key={c} className="row" style={{ gap: 6, minHeight: 24, cursor: mayAim ? 'pointer' : 'default' }}>
                <input type="checkbox" checked={ticked.includes(c)} disabled={!mayAim}
                       onChange={() => toggle(c)} style={{ width: 16, height: 16, flexShrink: 0 }} />
                <span style={{ fontSize: 12 }}>{humanize(c)}</span>
              </label>
          ))}
        </div>
        {mayAim ? (
          dirty && (
            <div className="row" style={{ gap: 6 }}>
              <button className="btn primary" disabled={saving}
                      title="Save what your role is flagged on"
                      onClick={save}>{saving ? 'Saving…' : 'Save my focus'}</button>
              <button className="btn compact" disabled={saving}
                      onClick={() => { setFocus(data.focus); setSaved(false); }}>Discard</button>
            </div>
          )
        ) : (
          // Disabled WITH A REASON — a dead control that will not say what
          // it is waiting for is a dead end.
          <p className="muted" style={{ margin: 0, fontSize: 11 }}>
            Aiming a role's attention rides <strong>Config — own role</strong>,
            which this sign-in does not hold. Tick it for {data.role || 'this role'} on
            4truck → Permissions, Inventory's Config column.
            {/* Named to the tick, not to the flag.  An owner meets this
                one: they hold Config — account-wide and Account, and on
                the dashboard the second crosses into any role, but a
                browser key does not carry it and must not.  So the answer
                is the matrix, which is where it belongs — and saying
                "your account has not given you the right" without saying
                WHICH right leaves them nowhere to go. */}
          </p>
        )}
        {saved && !dirty && (
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            Saved. Your screens stop flagging what you unticked.
          </p>
        )}
      </section>
      )}

      {/* No standalone "nothing is expected" line any more.  It said the
          same thing the section below says per type, and — since the panel
          learned to write the catalogue — its second half had become
          untrue: "the list is written on 4truck" is exactly what a holder
          no longer has to do.  The section's own empty state carries both
          the fact and the button, and the closing line at the bottom is
          what explains the absence to somebody who cannot. */}
      {/* One type at a time, the way the dashboard's dialog does it.  Two
          stacked lists made the shorter one read as a continuation of the
          longer, and a panel is 320px — the two together were most of a
          screenful of things that are not the type you came to check. */}
      <div className="row" style={{ gap: 4 }}>
        {data.vehicle_types.map((t) => (
          <button key={t} type="button" className={`chip ${type === t ? 'on' : ''}`}
                  role="radio" aria-checked={type === t}
                  onClick={() => setType(t)}>
            {t === 'truck' ? 'Trucks' : 'Trailers'}
          </button>
        ))}
      </div>
      {[type].map((t) => (
        <section key={t} style={{ display: 'grid', gap: 8 }}>
          <span className="muted eyebrow">Expected on every {t}</span>
          {/* The same act the dashboard does, behind the same flag.  Kept
              read-only for a while and then opened on the owner's call:
              what one screen can do the other must, or a person learns
              the feature twice. */}
          {rows.length === 0 ? (
            <div style={{ display: 'grid', gap: 8, justifyItems: 'start' }}>
              <p className="muted" style={{ margin: 0, fontSize: 12 }}>
                Nothing is expected, so no {t} is ever reported short.
              </p>
              {mayEdit && (
                <button className="btn primary"
                        onClick={() => setDraft((data.standard[t] ?? []).map((r) => ({ ...r })))}>
                  Start from the standard list
                </button>
              )}
            </div>
          ) : (
            <div style={{ display: 'grid', gap: 4 }}>
              {rows.map((r, n) => (
                <div key={`${r.category}-${n}`} className="row"
                     style={{ gap: 6, minHeight: 24, fontSize: 12, flexWrap: 'wrap' }}>
                  {mayEdit ? (
                    <>
                      <input className="input" aria-label="Category" value={r.category}
                             placeholder="camera, eld, fuel_card…"
                             // No `minWidth: 0`: it would let the one
                             // shrinkable field give everything while the
                             // fixed ones hold, which is exactly what
                             // collapsed the dashboard's label field.  The
                             // basis is a real floor and the row wraps.
                             style={{ flex: '1 1 7rem' }}
                             onChange={(e) => editRow(n, { category: e.target.value })} />
                      {/* The reader's name for the row.  The dashboard has
                          had this field since the start; the panel shipped
                          without it, so a row created here had no label and
                          fell back to the category key. */}
                      <input className="input" aria-label="What to call it" value={r.label}
                             placeholder={humanize(r.category) || 'What to call it'}
                             style={{ flex: '1 1 7rem' }}
                             onChange={(e) => editRow(n, { label: e.target.value })} />
                      <input className="input" aria-label="How many" type="number" min={1} max={99}
                             value={r.quantity} style={{ width: 56, flexShrink: 0 }}
                             onChange={(e) => editRow(n, { quantity: Math.max(1, Number(e.target.value) || 1) })} />
                      {/* minHeight on the LABEL, which is the target: the
                          box inside it is 16px and a 16px target is under
                          the floor.  The focus list above already does
                          this; this row was the one that forgot. */}
                      <label className="row" style={{ gap: 4, flexShrink: 0, fontSize: 11, minHeight: 24 }}>
                        <input type="checkbox" checked={r.required}
                               style={{ width: 16, height: 16 }}
                               onChange={(e) => editRow(n, { required: e.target.checked })} />
                        Required
                      </label>
                      <button className="btn compact" style={{ flexShrink: 0 }}
                              title={`Remove ${r.label || r.category}`}
                              aria-label={`Remove ${r.label || r.category}`}
                              onClick={() => setDraft(rows.filter((_, x) => x !== n))}>×</button>
                    </>
                  ) : (
                    <>
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
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
          {mayEdit && rows.length > 0 && (
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <button className="btn compact"
                      onClick={() => setDraft([...rows, { category: '', label: '', quantity: 1, required: true }])}>
                Add a row
              </button>
              <button className="btn compact"
                      onClick={() => setDraft((data.standard[t] ?? []).map((r) => ({ ...r })))}>
                Reset to standard
              </button>
            </div>
          )}
          {mayEdit && rowsDirty && (
            <div className="row" style={{ gap: 6 }}>
              <button className="btn primary" disabled={savingRows || rows.some((r) => !r.category.trim())}
                      title={rows.some((r) => !r.category.trim())
                        ? 'Every row needs a category' : 'Save this list'}
                      onClick={saveRows}>{savingRows ? 'Saving…' : 'Save the list'}</button>
              <button className="btn compact" disabled={savingRows}
                      onClick={() => setDraft(null)}>Discard</button>
            </div>
          )}
          {/* Saving the LIST said nothing while saving the focus said
              something — one surface, two acts, and only one of them
              acknowledged.  A save whose only visible result is a button
              disappearing reads as a press that did nothing. */}
          {mayEdit && rowsSaved && !rowsDirty && (
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              Saved. Every {type} is measured against this list from now on.
            </p>
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
