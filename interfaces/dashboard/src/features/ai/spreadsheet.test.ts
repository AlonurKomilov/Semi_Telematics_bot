import { describe, it, expect } from 'vitest';
import * as XLSX from 'xlsx';
import { workbookToCsvTexts, isSpreadsheetFile } from './spreadsheet';

function wbBytes(sheets: Record<string, unknown[][]>): ArrayBuffer {
  const wb = XLSX.utils.book_new();
  for (const [name, rows] of Object.entries(sheets)) {
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), name);
  }
  return XLSX.write(wb, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer;
}

describe('spreadsheet conversion (device-local xlsx → CSV text)', () => {
  it('converts a single-sheet workbook, keeping the file name', async () => {
    const data = wbBytes({
      Trucks: [
        ['Units', 'Fire extinguisher', 'Notes'],
        ['103 OSY', 'Good', 'remontda bolshi kere'],
        ['110', 'Not checked', ''],
      ],
    });
    const parts = await workbookToCsvTexts('Fleet sheet.xlsx', data);
    expect(parts).toHaveLength(1);
    expect(parts[0].name).toBe('Fleet sheet.xlsx');
    expect(parts[0].content).toContain('Units,Fire extinguisher,Notes');
    expect(parts[0].content).toContain('103 OSY,Good,remontda bolshi kere');
  });

  it('splits a multi-sheet workbook into named parts, skipping empty tabs', async () => {
    const data = wbBytes({
      Trucks: [['u'], ['22']],
      Empty: [[]],
      Trailers: [['u'], ['T-9']],
    });
    const parts = await workbookToCsvTexts('inv.xlsx', data);
    expect(parts.map((p) => p.name)).toEqual([
      'inv.xlsx — Trucks',
      'inv.xlsx — Trailers',
    ]);
  });

  it('quotes cells with embedded commas so the CSV round-trips', async () => {
    const data = wbBytes({
      S: [['u', 'note'], ['130', 'Driver kasal ekan, kamen v pochkah']],
    });
    const [part] = await workbookToCsvTexts('n.xlsx', data);
    expect(part.content).toContain('"Driver kasal ekan, kamen v pochkah"');
  });

  it('recognizes spreadsheet files by extension and MIME', () => {
    const f = (name: string, type = '') => ({ name, type }) as File;
    expect(isSpreadsheetFile(f('a.csv'))).toBe(true);
    expect(isSpreadsheetFile(f('Fleet sheet.xlsx'))).toBe(true);
    expect(isSpreadsheetFile(f('old.xls'))).toBe(true);
    expect(isSpreadsheetFile(f('blob', 'text/csv'))).toBe(true);
    expect(isSpreadsheetFile(f('report.pdf'))).toBe(false);
    expect(isSpreadsheetFile(f('notes.docx'))).toBe(false);
  });
});
