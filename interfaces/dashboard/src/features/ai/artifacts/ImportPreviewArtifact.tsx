/**
 * Shared `import_preview` artifact — ONE renderer for every import
 * target (Inventory today, any future adapter tomorrow; the universal
 * mechanism, per docs/architecture/ai-import-assistant.md §4).
 *
 * Shows the staged rows the user is about to approve (bounded display
 * sample — the executor writes the full staged set server-side), the
 * totals line, and the per-row skip report.  Renders directly above the
 * action_proposal approve card in the same tool result.
 *
 * EDITABLE while the linked proposal is pending: "Edit rows" loads the
 * FULL staged set from the server and lets the user correct a single
 * cell (or drop a row) — each change is validated server-side by the
 * import's own rules and lands in the same staged rows the no-body
 * approve executes, so what's shown stays exactly what's written.
 */
import { useState } from 'react';
import { AlertTriangle, Info, Pencil, X, Check } from 'lucide-react';
import DataGrid from '../../../components/datagrid';
import {
  aiGetActionRows, aiEditActionRow, aiRemoveActionRow,
} from '../../../api/client';
import { toneText } from '../../../lib/status';
import type { AnyColumn } from '../../../types';
import { registerArtifact } from './registry';
import { isImportPreview, type Artifact } from './types';

type Row = Record<string, unknown>;

/** The form-embedded row editor (raw <table> per the design system's
 *  line-item-editor exception — this is a form, not a data list). */
function EditableRows({
  proposalId, columns, rows, options, onRows, onCount,
}: {
  proposalId: string;
  columns: { key: string; label: string }[];
  rows: Row[];
  options: Record<string, string[]>;
  onRows: (rows: Row[]) => void;
  onCount: (n: number) => void;
}) {
  const [editing, setEditing] = useState<{ row: number; key: string } | null>(null);
  const [value, setValue] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  async function save(row: number, key: string, v: string) {
    setBusy(true); setErr('');
    try {
      const res = await aiEditActionRow(proposalId, row, { [key]: v });
      const next = [...rows];
      next[row] = res.row;
      onRows(next);
      onCount(res.to_import);
      setEditing(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Edit failed');
    } finally {
      setBusy(false);
    }
  }
  async function removeRow(row: number) {
    setBusy(true); setErr('');
    try {
      const res = await aiRemoveActionRow(proposalId, row);
      onRows(res.rows);
      onCount(res.to_import);
      setEditing(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Remove failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-1 overflow-x-auto rounded-md border border-border">
      <table className="w-full text-2xs">
        <thead>
          <tr className="bg-muted/60 text-left text-muted-foreground">
            {columns.map((c) => (
              <th key={c.key} className="px-2 py-1 font-medium">{c.label}</th>
            ))}
            <th className="w-6 px-1 py-1" aria-label="Remove row" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {columns.map((c) => {
                const isEditing = editing?.row === i && editing.key === c.key;
                const cell = String(r[c.key] ?? '');
                if (!isEditing) {
                  return (
                    <td key={c.key} className="px-0.5 py-0.5">
                      <button
                        onClick={() => { setEditing({ row: i, key: c.key }); setValue(cell); setErr(''); }}
                        className="w-full rounded px-1.5 py-0.5 text-left text-foreground hover:bg-muted transition-colors"
                        aria-label={`Edit ${c.label} of row ${i + 1}`}
                      >
                        {cell || <span className="text-muted-foreground/50">—</span>}
                      </button>
                    </td>
                  );
                }
                const opts = options[c.key];
                return (
                  <td key={c.key} className="px-0.5 py-0.5">
                    <div className="flex items-center gap-1">
                      {opts?.length ? (
                        <select
                          autoFocus
                          value={value}
                          disabled={busy}
                          onChange={(e) => void save(i, c.key, e.target.value)}
                          className="w-full rounded border border-ring bg-card px-1 py-0.5 text-2xs text-foreground"
                          aria-label={`${c.label} value`}
                        >
                          {!opts.includes(value) && <option value={value}>{value}</option>}
                          {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input
                          autoFocus
                          value={value}
                          disabled={busy}
                          onChange={(e) => setValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void save(i, c.key, value);
                            if (e.key === 'Escape') setEditing(null);
                          }}
                          className="w-full rounded border border-ring bg-card px-1 py-0.5 text-2xs text-foreground"
                          aria-label={`${c.label} value`}
                        />
                      )}
                      <button
                        onClick={() => void save(i, c.key, value)}
                        disabled={busy}
                        aria-label="Save cell"
                        className="shrink-0 text-muted-foreground hover:text-foreground transition-colors"
                      >
                        <Check size={12} aria-hidden />
                      </button>
                    </div>
                  </td>
                );
              })}
              <td className="px-1 py-0.5 text-center">
                <button
                  onClick={() => void removeRow(i)}
                  disabled={busy}
                  aria-label={`Remove row ${i + 1}`}
                  className="text-muted-foreground hover:text-destructive transition-colors"
                >
                  <X size={12} aria-hidden />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {err && (
        <div className="border-t border-border px-2 py-1 text-3xs text-destructive" role="alert">
          {err}
        </div>
      )}
    </div>
  );
}

function ImportPreviewView({ artifact }: { artifact: Artifact }) {
  const preview = isImportPreview(artifact) ? artifact : null;
  const [serverRows, setServerRows] = useState<Row[] | null>(null);
  const [editOptions, setEditOptions] = useState<Record<string, string[]>>({});
  const [toImport, setToImport] = useState<number | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [loadErr, setLoadErr] = useState('');
  if (!preview) return null;

  const columns: AnyColumn[] = preview.columns.map((c) => ({
    key: c.key,
    label: c.label,
    sortable: true,
  }));
  const { total, shown, skipped } = preview.totals;
  const liveTotal = toImport ?? total;

  async function startEdit() {
    if (!preview?.proposal_id) return;
    setLoadErr('');
    try {
      const res = await aiGetActionRows(preview.proposal_id);
      setServerRows(res.rows);
      setEditOptions(res.edit_options || {});
      setToImport(res.to_import);
      setEditMode(true);
    } catch (e) {
      // 409 = no longer pending; 404 = pruned — editing is simply closed.
      setLoadErr(e instanceof Error ? e.message : 'Editing is not available');
    }
  }

  return (
    <div className="mt-2">
      {preview.title && (
        <div className="mb-1 text-2xs font-medium text-muted-foreground">
          {preview.title}
        </div>
      )}
      {editMode && serverRows && preview.proposal_id ? (
        <EditableRows
          proposalId={preview.proposal_id}
          columns={preview.columns}
          rows={serverRows}
          options={editOptions}
          onRows={setServerRows}
          onCount={setToImport}
        />
      ) : (
        <DataGrid
      // The conversation is what the reader scrolls — a table that
      // scrolled inside itself would trap them mid-answer.
      autoFit={false}
          columns={columns}
          data={serverRows ?? preview.rows}
          enableToolbar={false}
          enablePagination={false}
        />
      )}
      <div className="mt-1 flex flex-wrap items-center gap-2 text-3xs text-muted-foreground">
        <span>
          {serverRows == null && shown < total
            ? `Showing ${shown} of ${total} rows to import.`
            : `${liveTotal} rows to import.`}
          {skipped > 0 && ` ${skipped} skipped.`}
        </span>
        {preview.proposal_id && !editMode && (
          <button
            onClick={() => void startEdit()}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-muted hover:text-foreground transition-colors"
          >
            <Pencil size={12} aria-hidden /> Edit rows
          </button>
        )}
        {editMode && (
          <button
            onClick={() => setEditMode(false)}
            className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 hover:bg-muted hover:text-foreground transition-colors"
          >
            <Check size={12} aria-hidden /> Done editing
          </button>
        )}
        {loadErr && <span className="text-destructive">{loadErr}</span>}
      </div>
      {(preview.notices?.length ?? 0) > 0 && (
        <div className="mt-1.5 rounded-md border border-info-bd bg-info-bg p-2">
          <div className={`mb-1 inline-flex items-center gap-1 text-3xs font-medium ${toneText('info')}`}>
            <Info size={12} aria-hidden /> Adjusted during validation
          </div>
          <ul className="space-y-0.5 text-3xs text-muted-foreground">
            {preview.notices!.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {preview.skipped.length > 0 && (
        <div className="mt-1.5 rounded-md border border-warn-bd bg-warn-bg p-2">
          <div className={`mb-1 inline-flex items-center gap-1 text-3xs font-medium ${toneText('warn')}`}>
            <AlertTriangle size={12} aria-hidden /> Skipped rows
          </div>
          <ul className="space-y-0.5 text-3xs text-muted-foreground">
            {preview.skipped.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
            {preview.skipped_truncated && (
              <li>…and {skipped - preview.skipped.length} more.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

registerArtifact('import_preview', (a) => <ImportPreviewView artifact={a} />);

export default ImportPreviewView;
