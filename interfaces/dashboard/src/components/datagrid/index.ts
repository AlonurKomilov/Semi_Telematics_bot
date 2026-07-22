// Public entry point for the DataGrid feature.  The Column*Menu files
// (ColumnHeaderMenu / ColumnFilterMenu / ManageColumnsMenu) are internal
// implementation detail — DataGrid.tsx imports them directly and nothing
// outside this folder should.  Consumers import from '.../components/datagrid'.
export { default } from './DataGrid';
export type { BulkAction, DataGridSegment } from './DataGrid';
