/**
 * An approved action's files must reach the right record.
 *
 * The uploader was hardcoded to the work-order attachments endpoint,
 * so `file_vehicle_document` proposed, the human approved — and
 * NOTHING uploaded.  The action reported success and no document
 * appeared, which is the worst kind of broken: it looks like it worked.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const apiFetch = vi.fn();
vi.mock('../../api/client', () => ({ apiFetch: (...a: unknown[]) => apiFetch(...a) }));

const held = vi.fn();
vi.mock('./attachmentStore', () => ({ findHeldAttachments: () => held() }));

import { uploadSourceFiles, uploadSourceFilesToWorkOrder } from './sourceFileUpload';

const IMAGE = {
  name: 'cabcard.jpg', kind: 'image',
  content: 'data:image/jpeg;base64,/9j/4AAQ',
};

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({ ok: true });
  held.mockReturnValue([IMAGE]);
  global.fetch = vi.fn().mockResolvedValue({
    blob: async () => new Blob(['x'], { type: 'image/jpeg' }),
  }) as unknown as typeof fetch;
});

describe('post-approve upload routing', () => {
  it('sends a vehicle document to that truck, with the approved dates', async () => {
    const out = await uploadSourceFiles({
      target_type: 'vehicle_document', target_id: 42,
      doc_type: 'cab_card', expires_at: '2027-01-31',
    }, ['cabcard.jpg']);

    expect(out.uploaded).toEqual(['cabcard.jpg']);
    expect(apiFetch.mock.calls[0][0]).toBe('/vehicles/registry/42/documents');
    const fd = apiFetch.mock.calls[0][1].body as FormData;
    // The expiry the HUMAN approved is the expiry that gets stored —
    // without it the whole warning chain reads an empty field.
    expect(fd.get('doc_type')).toBe('cab_card');
    expect(fd.get('expires_at')).toBe('2027-01-31');
  });

  it('still sends a work order to attachments, with no doc fields', async () => {
    await uploadSourceFiles(
      { target_type: 'work_order', target_id: 7 }, ['cabcard.jpg']);
    expect(apiFetch.mock.calls[0][0]).toBe('/work-orders/7/attachments?kind=photo');
    expect((apiFetch.mock.calls[0][1].body as FormData).get('doc_type')).toBeNull();
  });

  it('omits a date the model could not read rather than sending empty', async () => {
    await uploadSourceFiles(
      { target_type: 'vehicle_document', target_id: 42, doc_type: 'title' },
      ['cabcard.jpg']);
    const fd = apiFetch.mock.calls[0][1].body as FormData;
    expect(fd.get('expires_at')).toBeNull();
  });

  it('uploads nothing for a result that owns no files', async () => {
    const out = await uploadSourceFiles(
      { target_type: 'something_else', target_id: 1 }, ['cabcard.jpg']);
    expect(apiFetch).not.toHaveBeenCalled();
    expect(out.missing).toEqual(['cabcard.jpg']);
  });

  it('reports a PDF as missing instead of uploading its extracted text', async () => {
    // The store keeps only TEXT for a PDF — the bytes are genuinely not
    // on hand, so "widening the filter" would upload a text file
    // wearing a PDF's name.
    held.mockReturnValue([{ name: 'reg.pdf', kind: 'text', content: 'REGISTRATION…' }]);
    const out = await uploadSourceFiles(
      { target_type: 'vehicle_document', target_id: 42 }, ['reg.pdf']);
    expect(out.missing).toEqual(['reg.pdf']);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('keeps the old work-order entry point working', async () => {
    await uploadSourceFilesToWorkOrder(9, ['cabcard.jpg']);
    expect(apiFetch.mock.calls[0][0]).toBe('/work-orders/9/attachments?kind=photo');
  });
});
