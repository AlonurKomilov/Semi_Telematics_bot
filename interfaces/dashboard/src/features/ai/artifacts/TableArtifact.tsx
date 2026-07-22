/**
 * Built-in `table` artifact — renders through the app's DataGrid so an
 * AI-produced table reads exactly like every other table (tokens, zebra,
 * theme).  Minimal chrome: no toolbar / pagination (it's a glance, not a
 * managed list).  Registered at module load via the artifacts barrel.
 */
import DataGrid from '../../../components/datagrid';
import type { AnyColumn } from '../../../types';
import { registerArtifact } from './registry';
import { isTable, type Artifact } from './types';

function TableArtifactView({ artifact }: { artifact: Artifact }) {
  if (!isTable(artifact)) return null;
  const columns: AnyColumn[] = artifact.columns.map((c) => ({
    key: c.key,
    label: c.label,
    sortable: true,
  }));
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
    </div>
  );
}

registerArtifact('table', (a) => <TableArtifactView artifact={a} />);

export default TableArtifactView;
