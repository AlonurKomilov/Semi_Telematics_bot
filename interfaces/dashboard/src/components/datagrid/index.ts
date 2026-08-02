// Public entry point for the DataGrid feature.  The Column*Menu files
// (ColumnHeaderMenu / ColumnFilterMenu / ManageColumnsMenu) are internal
// implementation detail — DataGrid.tsx imports them directly and nothing
// outside this folder should.  Consumers import from '.../components/datagrid'.
export { default, TAB_PREFIX } from './DataGrid';
export type { BulkAction, DataGridSegment } from './DataGrid';

// A CONTROLLED page that filters client-side has to replay a saved tab's
// scope itself — the grid narrows its own rows, but any second view the
// page renders (Applications' Board) would otherwise show a different
// population from the table.  Server-filtered pages don't need this:
// they put the tab's captured criteria into the query instead.
export { tabMatch } from './tabs/savedTabs';
