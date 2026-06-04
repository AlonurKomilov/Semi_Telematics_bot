/**
 * Operator-only persona-group + alert-routing-mode editor.
 *
 * Surfaces three things on the account-detail page:
 *
 *   1. Current routing mode (single_group / per_persona_groups) and a
 *      toggle to flip between them.
 *   2. The full persona ↔ Telegram-group binding table (one row per
 *      persona) with inline upsert / soft-disable / delete.
 *   3. A subtle hint when the mode is per_persona_groups but the
 *      operational personas (dispatcher / safety / fleet / hr) are not
 *      all registered — the resolver's partial-config fallback covers
 *      this gracefully, but the operator should know they're
 *      mid-migration.
 *
 * Backend endpoints: /system/accounts/{id}/alert-routing/*.
 */

import { useEffect, useState } from 'react';
import { apiJSON, ApiError } from '../api/client';
import type {
  AlertRoutingConfig, AlertRoutingMode, PersonaKey, PersonaGroupRow,
} from '../types';

// Display order for the persona table.  Owner/admin first because it's
// the aggregate (the one universal across modes), then operational
// personas in the order operators tend to onboard them.
const PERSONA_ORDER: PersonaKey[] = [
  'owner_admin', 'dispatcher', 'safety', 'fleet', 'hr',
];

const PERSONA_LABEL: Record<PersonaKey, string> = {
  owner_admin: 'Owner / Admin (aggregate)',
  dispatcher:  'Dispatcher',
  safety:      'Safety',
  fleet:       'Fleet',
  hr:          'HR',
};

const PERSONA_HINT: Record<PersonaKey, string> = {
  owner_admin: 'Cross-cutting digest. Receives every CRITICAL alert.',
  dispatcher:  'Live ops: geofence, parking, fuel.',
  safety:      'Driver behavior: events, camera.',
  fleet:       'Mechanical: faults, health, scorecard, maintenance.',
  hr:          'Driver documents and expiries.',
};

export default function AlertRoutingCard({ accountId }: { accountId: number }) {
  const [config, setConfig] = useState<AlertRoutingConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [modeBusy, setModeBusy] = useState(false);
  const [editingPersona, setEditingPersona] = useState<PersonaKey | null>(null);

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const data = await apiJSON<AlertRoutingConfig>(
        `/system/accounts/${accountId}/alert-routing`,
      );
      setConfig(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load routing config');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [accountId]);

  const toggleMode = async () => {
    if (!config || modeBusy) return;
    const next: AlertRoutingMode =
      config.alert_routing_mode === 'single_group'
        ? 'per_persona_groups' : 'single_group';
    if (!confirm(
      `Switch routing mode to "${next}"?\n\n` +
      (next === 'per_persona_groups'
        ? 'Per-persona-groups mode posts alerts to flat group chats by ' +
          'persona instead of the legacy forum-topic layout.  If no ' +
          'persona groups are configured yet, the resolver falls back ' +
          'to the legacy lookup during partial migration.'
        : 'Single-group mode posts to a forum with one topic per ' +
          'alert_type — the pre-3-D behavior.'),
    )) return;
    setModeBusy(true);
    try {
      await apiJSON(`/system/accounts/${accountId}/alert-routing/mode`, {
        method: 'PUT', body: { mode: next },
      });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Mode flip failed');
    } finally {
      setModeBusy(false);
    }
  };

  if (loading) {
    return (
      <Card title="Alert Routing">
        <div className="text-sm text-slate-500 py-4">Loading…</div>
      </Card>
    );
  }
  if (error || !config) {
    return (
      <Card title="Alert Routing">
        <div className="text-sm text-danger py-2">{error || 'No data'}</div>
      </Card>
    );
  }

  const isPerPersona = config.alert_routing_mode === 'per_persona_groups';
  const operationalMissing = isPerPersona
    ? (['dispatcher', 'safety', 'fleet', 'hr'] as PersonaKey[])
        .filter(p => !config.personas[p] || !config.personas[p]!.is_active)
    : [];

  return (
    <Card
      title="Alert Routing"
      actions={
        <button
          onClick={toggleMode}
          disabled={modeBusy}
          className="text-xs px-2.5 py-1 rounded border border-slate-700 hover:border-slate-500 disabled:opacity-50"
          title="Switch routing mode for this account"
        >
          {modeBusy ? '…' : `Switch to ${isPerPersona ? 'single_group' : 'per_persona_groups'}`}
        </button>
      }
    >
      <div className="text-xs text-slate-400 mb-3">
        <span className="text-slate-500">Mode: </span>
        <span className={`font-mono ${isPerPersona ? 'text-emerald-400' : 'text-slate-300'}`}>
          {config.alert_routing_mode}
        </span>
        {isPerPersona && operationalMissing.length > 0 && (
          <div className="mt-1.5 text-amber-400/80">
            ⚠ {operationalMissing.length} operational persona(s) unregistered:
            {' '}{operationalMissing.join(', ')}.
            <span className="text-slate-500"> Resolver falls back to legacy until configured.</span>
          </div>
        )}
      </div>

      <table className="w-full text-xs">
        <thead className="text-slate-500 border-b border-slate-800">
          <tr>
            <th className="text-left py-1.5 pr-2 font-normal">Persona</th>
            <th className="text-left py-1.5 pr-2 font-normal">Chat</th>
            <th className="text-left py-1.5 pr-2 font-normal">Status</th>
            <th className="text-right py-1.5 font-normal">Actions</th>
          </tr>
        </thead>
        <tbody>
          {PERSONA_ORDER.map(persona => (
            <PersonaRow
              key={persona}
              persona={persona}
              row={config.personas[persona]}
              isEditing={editingPersona === persona}
              accountId={accountId}
              onEdit={() => setEditingPersona(persona)}
              onClose={() => setEditingPersona(null)}
              onChanged={async () => { setEditingPersona(null); await load(); }}
              modeIsPerPersona={isPerPersona}
            />
          ))}
        </tbody>
      </table>
    </Card>
  );
}


function PersonaRow({
  persona, row, isEditing, accountId,
  onEdit, onClose, onChanged, modeIsPerPersona,
}: {
  persona: PersonaKey;
  row: PersonaGroupRow | null;
  isEditing: boolean;
  accountId: number;
  onEdit: () => void;
  onClose: () => void;
  onChanged: () => Promise<void>;
  modeIsPerPersona: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [rowError, setRowError] = useState('');

  const toggleActive = async () => {
    if (!row || busy) return;
    setBusy(true); setRowError('');
    try {
      await apiJSON(
        `/system/accounts/${accountId}/alert-routing/personas/${persona}/active`,
        { method: 'PATCH', body: { is_active: !row.is_active } },
      );
      await onChanged();
    } catch (e) {
      setRowError(e instanceof Error ? e.message : 'Toggle failed');
    } finally { setBusy(false); }
  };

  const remove = async () => {
    if (!row || busy) return;
    if (!confirm(`Permanently delete the ${persona} group binding?\n\nUse Disable instead if you might re-enable it later.`)) return;
    setBusy(true); setRowError('');
    try {
      await apiJSON(
        `/system/accounts/${accountId}/alert-routing/personas/${persona}`,
        { method: 'DELETE' },
      );
      await onChanged();
    } catch (e) {
      setRowError(e instanceof Error ? e.message : 'Delete failed');
    } finally { setBusy(false); }
  };

  return (
    <>
      <tr className={!modeIsPerPersona ? 'text-slate-600' : ''}>
        <td className="py-1.5 pr-2 align-top">
          <div className="text-slate-200">{PERSONA_LABEL[persona]}</div>
          <div className="text-[10px] text-slate-500 leading-snug">
            {PERSONA_HINT[persona]}
          </div>
        </td>
        <td className="py-1.5 pr-2 align-top font-mono">
          {row ? (
            <>
              <div className="text-slate-300">{row.chat_id}</div>
              {row.chat_title && (
                <div className="text-[10px] text-slate-500 font-sans">{row.chat_title}</div>
              )}
            </>
          ) : <span className="text-slate-600">—</span>}
        </td>
        <td className="py-1.5 pr-2 align-top">
          {row ? (
            row.is_active ? (
              <span className="text-emerald-400">active</span>
            ) : (
              <span className="text-slate-500">disabled</span>
            )
          ) : <span className="text-slate-600">unregistered</span>}
        </td>
        <td className="py-1.5 text-right align-top whitespace-nowrap">
          {!isEditing && (
            <>
              <button
                onClick={onEdit}
                disabled={busy}
                className="text-accent hover:underline text-xs disabled:opacity-50"
              >
                {row ? 'edit' : 'add'}
              </button>
              {row && (
                <>
                  <span className="text-slate-700 mx-1.5">·</span>
                  <button
                    onClick={toggleActive}
                    disabled={busy}
                    className="text-xs hover:underline disabled:opacity-50 text-slate-400"
                  >
                    {row.is_active ? 'disable' : 'enable'}
                  </button>
                  <span className="text-slate-700 mx-1.5">·</span>
                  <button
                    onClick={remove}
                    disabled={busy}
                    className="text-danger hover:underline text-xs disabled:opacity-50"
                  >
                    delete
                  </button>
                </>
              )}
            </>
          )}
        </td>
      </tr>
      {rowError && (
        <tr>
          <td colSpan={4} className="pb-1 text-[11px] text-danger">{rowError}</td>
        </tr>
      )}
      {isEditing && (
        <tr>
          <td colSpan={4} className="pb-3">
            <PersonaEditForm
              accountId={accountId}
              persona={persona}
              row={row}
              onClose={onClose}
              onSaved={onChanged}
            />
          </td>
        </tr>
      )}
    </>
  );
}


function PersonaEditForm({
  accountId, persona, row, onClose, onSaved,
}: {
  accountId: number;
  persona: PersonaKey;
  row: PersonaGroupRow | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [chatId, setChatId] = useState(row ? String(row.chat_id) : '');
  const [chatTitle, setChatTitle] = useState(row?.chat_title ?? '');
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState('');

  const save = async () => {
    setErr('');
    const parsed = Number(chatId.trim());
    if (!Number.isInteger(parsed) || parsed === 0) {
      setErr('chat_id must be a non-zero integer (negative for Telegram groups)');
      return;
    }
    setSaving(true);
    try {
      await apiJSON(
        `/system/accounts/${accountId}/alert-routing/personas/${persona}`,
        {
          method: 'PUT',
          body: { chat_id: parsed, chat_title: chatTitle.trim() },
        },
      );
      await onSaved();
    } catch (e) {
      if (e instanceof ApiError && e.status === 400) setErr(e.message);
      else setErr(e instanceof Error ? e.message : 'Save failed');
    } finally { setSaving(false); }
  };

  return (
    <div className="bg-slate-950 border border-slate-700 rounded p-3 mt-1">
      <div className="text-[11px] text-slate-400 mb-2">
        {row ? 'Update' : 'Register'} {PERSONA_LABEL[persona]} group binding
      </div>
      <div className="flex gap-2 flex-wrap items-end">
        <label className="block">
          <span className="block text-[10px] text-slate-500 mb-0.5">chat_id</span>
          <input
            value={chatId}
            onChange={e => setChatId(e.target.value)}
            placeholder="-1001234567890"
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs font-mono w-44 text-slate-200"
            autoFocus
          />
        </label>
        <label className="block flex-1 min-w-[12rem]">
          <span className="block text-[10px] text-slate-500 mb-0.5">chat title (optional)</span>
          <input
            value={chatTitle}
            onChange={e => setChatTitle(e.target.value)}
            placeholder="Fleet Operations"
            className="bg-slate-900 border border-slate-700 rounded px-2 py-1 text-xs w-full text-slate-200"
          />
        </label>
        <div className="flex gap-2">
          <button
            onClick={save}
            disabled={saving || !chatId.trim()}
            className="text-xs px-3 py-1 rounded bg-accent text-slate-950 font-medium hover:opacity-90 disabled:opacity-50"
          >
            {saving ? 'saving…' : 'save'}
          </button>
          <button
            onClick={onClose}
            disabled={saving}
            className="text-xs px-3 py-1 rounded border border-slate-700 text-slate-300 hover:border-slate-500 disabled:opacity-50"
          >
            cancel
          </button>
        </div>
      </div>
      {err && (
        <div className="mt-2 text-[11px] text-danger">{err}</div>
      )}
      <div className="mt-2 text-[10px] text-slate-500">
        Get chat_id by adding the tenant bot to the Telegram group as
        admin, then running <span className="font-mono">/groupinfo</span>{' '}
        in the group.  Groups use negative IDs (e.g. <span className="font-mono">-1001234567890</span>).
      </div>
    </div>
  );
}


// Local Card duplicates AccountDetail.tsx's Card to keep this component
// drop-in importable.  Style is intentionally identical so it docks
// cleanly into the grid layout.
function Card({ title, actions, children }: {
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="bg-slate-900 border border-slate-800 rounded-lg p-4">
      <header className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  );
}
