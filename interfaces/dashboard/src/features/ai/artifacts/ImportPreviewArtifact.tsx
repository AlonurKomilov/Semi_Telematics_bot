/**
 * Shared `import_preview` artifact — ONE renderer for every import
 * target (Inventory today, any future adapter tomorrow; the universal
 * mechanism, per docs/architecture/ai-import-assistant.md §4).
 *
 * Shows the staged rows the user is about to approve (bounded display
 * sample — the executor writes the full staged set server-side), the
 * totals line, and the per-row skip report.  Renders directly above the
 * action_proposal approve card in the same tool result.
 */
import { AlertTriangle, Info } from 'lucide-react';
import DataGrid from '../../../components/DataGrid';
import { toneText } from '../../../lib/status';
import type { AnyColumn } from '../../../types';
import { registerArtifact } from './registry';
import { isImportPreview, type Artifact } from './types';

function ImportPreviewView({ artifact }: { artifact: Artifact }) {
  if (!isImportPreview(artifact)) return null;
  const columns: AnyColumn[] = artifact.columns.map((c) => ({
    key: c.key,
    label: c.label,
    sortable: true,
  }));
  const { total, shown, skipped } = artifact.totals;
  return (
    <div className="mt-2">
      {artifact.title && (
        <div className="mb-1 text-2xs font-medium text-muted-foreground">
          {artifact.title}
        </div>
      )}
      <DataGrid
        columns={columns}
        data={artifact.rows}
        enableToolbar={false}
        enablePagination={false}
      />
      <div className="mt-1 text-3xs text-muted-foreground">
        {shown < total ? `Showing ${shown} of ${total} rows to import.` : `${total} rows to import.`}
        {skipped > 0 && ` ${skipped} skipped.`}
      </div>
      {(artifact.notices?.length ?? 0) > 0 && (
        <div className="mt-1.5 rounded-md border border-info-bd bg-info-bg p-2">
          <div className={`mb-1 inline-flex items-center gap-1 text-3xs font-medium ${toneText('info')}`}>
            <Info size={12} aria-hidden /> Adjusted during validation
          </div>
          <ul className="space-y-0.5 text-3xs text-muted-foreground">
            {artifact.notices!.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {artifact.skipped.length > 0 && (
        <div className="mt-1.5 rounded-md border border-warn-bd bg-warn-bg p-2">
          <div className={`mb-1 inline-flex items-center gap-1 text-3xs font-medium ${toneText('warn')}`}>
            <AlertTriangle size={12} aria-hidden /> Skipped rows
          </div>
          <ul className="space-y-0.5 text-3xs text-muted-foreground">
            {artifact.skipped.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
            {artifact.skipped_truncated && (
              <li>…and {skipped - artifact.skipped.length} more.</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

registerArtifact('import_preview', (a) => <ImportPreviewView artifact={a} />);

export default ImportPreviewView;
