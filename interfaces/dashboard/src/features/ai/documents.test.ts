import { describe, it, expect } from 'vitest';
import { fileToAttachmentParts, isDocumentFile, isImageFile } from './documents';

describe('document dispatcher (attach lanes)', () => {
  it('routes CSV to the importable sheet lane — even with a text/plain MIME', async () => {
    // Windows commonly stamps .csv as text/plain; it must stay importable.
    const f = new File(['u,item\n22,Dashcam\n'], 'inv.csv', { type: 'text/plain' });
    const parts = await fileToAttachmentParts(f);
    expect(parts).toHaveLength(1);
    expect(parts[0].kind).toBe('sheet');
    expect(parts[0].content).toContain('22,Dashcam');
  });

  it('routes .txt to the read-only text lane', async () => {
    const f = new File(['Brake torque: 450 ft-lb'], 'specs.txt', { type: 'text/plain' });
    const [part] = await fileToAttachmentParts(f);
    expect(part.kind).toBe('text');
    expect(part.content).toBe('Brake torque: 450 ft-lb');
  });

  it('routes Excel through the sheet converter with kind=sheet', async () => {
    const XLSX = await import('xlsx');
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet([['u'], ['22']]), 'S');
    const data = XLSX.write(wb, { type: 'array', bookType: 'xlsx' }) as ArrayBuffer;
    const f = new File([data], 'fleet.xlsx');
    const [part] = await fileToAttachmentParts(f);
    expect(part.kind).toBe('sheet');
    expect(part.name).toBe('fleet.xlsx');
  });

  it('recognizes document + image files and rejects the rest', () => {
    const f = (name: string, type = '') => ({ name, type }) as File;
    expect(isDocumentFile(f('a.pdf'))).toBe(true);
    expect(isDocumentFile(f('blob', 'application/pdf'))).toBe(true);
    expect(isDocumentFile(f('a.txt'))).toBe(true);
    expect(isDocumentFile(f('a.csv'))).toBe(true);
    expect(isDocumentFile(f('a.xlsx'))).toBe(true);
    expect(isDocumentFile(f('a.png', 'image/png'))).toBe(true);
    expect(isDocumentFile(f('image.png', 'image/png'))).toBe(true);   // pasted screenshot
    expect(isDocumentFile(f('a.webp'))).toBe(true);
    expect(isDocumentFile(f('a.docx'))).toBe(false);
    expect(isDocumentFile(f('a.mp4', 'video/mp4'))).toBe(false);
    expect(isImageFile(f('a.jpg'))).toBe(true);
    expect(isImageFile(f('a.csv'))).toBe(false);
  });
});
