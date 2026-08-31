/**
 * The upload must send the expiry date.
 *
 * The first flow sent only the file and its type, so no document could
 * ever carry an expiry — and the alert, the warn/danger tones and the
 * Expiring / Expired tabs were all reading a field the UI never
 * collected.  A whole compliance feature dead on arrival behind a
 * control that looked finished.  This is the test that would have
 * caught it.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';

afterEach(cleanup);

const apiFetch = vi.fn();
vi.mock('../../../api/client', () => ({
  apiFetch: (...a: unknown[]) => apiFetch(...a),
  apiJSON: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import UploadDocumentDialog from './UploadDocumentDialog';

const VEHICLE = { registry_id: 42, name: '110', company: 'PTG' };

beforeEach(() => {
  apiFetch.mockReset();
  apiFetch.mockResolvedValue({ ok: true, json: async () => ({}) });
});

function open(extra: Record<string, unknown> = {}) {
  return render(
    <UploadDocumentDialog
      open onClose={() => {}} onUploaded={() => {}}
      vehicle={VEHICLE} {...extra}
    />,
  );
}

const pickFile = () => {
  const input = document.querySelector(
    'input[type="file"]') as HTMLInputElement;
  const file = new File(['%PDF-1.4'], 'insurance.pdf',
                        { type: 'application/pdf' });
  Object.defineProperty(input, 'files', { value: [file] });
  fireEvent.change(input);
};

const setDate = (label: string, value: string) => {
  const inputs = [...document.querySelectorAll('input[type="date"]')];
  const idx = label === 'Expires' ? 1 : 0;
  fireEvent.change(inputs[idx], { target: { value } });
};

const submit = () =>
  fireEvent.click(screen.getByRole('button', { name: /^upload$/i }));

describe('Upload document', () => {
  it('sends the expiry date the whole warning chain depends on', async () => {
    open();
    pickFile();
    setDate('Expires', '2027-01-31');
    submit();

    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const body = apiFetch.mock.calls[0][1].body as FormData;
    expect(body.get('expires_at')).toBe('2027-01-31');
    expect(body.get('doc_type')).toBe('registration');
    expect((body.get('file') as File).name).toBe('insurance.pdf');
  });

  it('omits the dates rather than sending empty ones', async () => {
    // A title never expires.  An empty string would store as a date
    // nobody chose, and the Expired tab would then be a lie.
    open();
    pickFile();
    submit();

    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    const body = apiFetch.mock.calls[0][1].body as FormData;
    expect(body.get('expires_at')).toBeNull();
    expect(body.get('issued_at')).toBeNull();
  });

  it('posts to the vehicle it was opened for', async () => {
    open();
    pickFile();
    submit();
    await waitFor(() => expect(apiFetch).toHaveBeenCalled());
    expect(apiFetch.mock.calls[0][0]).toBe('/vehicles/registry/42/documents');
  });

  it('refuses to send without a file, and says which field', async () => {
    open();
    submit();
    expect(await screen.findByText(/choose a file/i)).toBeTruthy();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('asks for the vehicle when opened from the fleet page', async () => {
    render(
      <UploadDocumentDialog
        open onClose={() => {}} onUploaded={() => {}}
        vehicles={[VEHICLE, { registry_id: 43, name: '111', company: 'OSY' }]}
      />,
    );
    pickFile();
    submit();
    // No truck chosen yet — the upload must not guess one.
    expect(await screen.findByText(/pick the vehicle/i)).toBeTruthy();
    expect(apiFetch).not.toHaveBeenCalled();
  });
});
