import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createInstance } from 'i18next';
import { I18nextProvider } from 'react-i18next';
import type { ReactNode } from 'react';
import en from '@/locales/en.json';
import { Composer } from './Composer';
import { GroupDialog } from './GroupDialog';
import { chatApi, ApiError } from './api';
import { ChatSession } from './session';
import { batch, conversation, draft, fakeApi, message } from './fixtures';
const i18n = createInstance();
await i18n.init({ lng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } });
const wrapper = ({ children }: { children: ReactNode }) => <I18nextProvider i18n={i18n}>{children}</I18nextProvider>;
const sessions: ChatSession[] = [];
beforeEach(() => { vi.spyOn(chatApi, 'saveDraft').mockImplementation(async (_id, value) => ({ ...draft, body: value.body, version: value.expected_version + 1 })); });
afterEach(() => { sessions.splice(0).forEach(s => s.stop()); vi.restoreAllMocks(); });
async function composer(sendAllowed = true, initialDraft = draft) {
  const s = new ChatSession(fakeApi({ draft: vi.fn(async () => initialDraft), events: vi.fn(async () => batch({ caller: { group_role: 'member' as const, actions: sendAllowed ? ['send' as const] : [] } })), conversation: vi.fn(async () => ({ ...conversation, caller: { group_role: 'member' as const, actions: sendAllowed ? ['send' as const] : [] } })) }), null);
  sessions.push(s); s.select('room-a'); await waitFor(() => expect(s.getSnapshot().room).not.toBeNull());
  const guard = vi.fn();
  const view = render(<Composer session={s} room={s.getSnapshot().room!} reply={null} editing={null} onClear={vi.fn()} registerGuard={guard} />, { wrapper });
  return { session: s, view };
}
describe('composer interactions', () => {
  it('requires an unavailable restored quote to be explicitly cleared before sending', async () => {
    const send = vi.spyOn(chatApi, 'send').mockResolvedValue({ message: message(), replayed: false, draft: { ...draft, version: 8 } });
    await composer(true, { ...draft, body: 'Unfinished reply', version: 6, reply: { status: 'unavailable' } });
    expect(screen.getByText('Quoted message unavailable')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
    expect(send.mock.calls[0][1]).toMatchObject({ reply_to_id: null, draft_version: 7 });
    await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(''));
  });

  it('uses server actions for a read-only member', async () => {
    await composer(false); expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Send' }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText('Only group administrators can send messages.')).toBeTruthy();
  });
  it('does not send while composing an IME character or inserting a newline', async () => {
    const send = vi.spyOn(chatApi, 'send').mockResolvedValue({ message: message(), replayed: false }); await composer();
    const input = screen.getByRole('textbox'); fireEvent.change(input, { target: { value: '你好' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true, keyCode: 229 }); fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' }); await waitFor(() => expect(send).toHaveBeenCalledTimes(1));
  });
  it('retains the exact payload/key when a committed send response might have been lost', async () => {
    const send = vi.spyOn(chatApi, 'send').mockRejectedValueOnce(new TypeError('network')).mockResolvedValueOnce({ message: message(1, { author: { id: 1, display_name: 'Me' } }), replayed: true });
    await composer(); fireEvent.change(screen.getByRole('textbox'), { target: { value: '  Retry safely  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' })); await waitFor(() => expect(screen.getByRole('button', { name: 'Retry send' })).toBeTruthy());
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: 'Retry send' })); await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[0][1]).toEqual(send.mock.calls[1][1]); await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(''));
  });
  it.each(['', 'New draft from another device'])('retries after a live draft clear without losing the newer draft: %s', async (remoteBody) => {
    const remoteDraft = { ...draft, body: remoteBody, version: remoteBody ? 3 : 2 };
    const send = vi.spyOn(chatApi, 'send').mockRejectedValueOnce(new TypeError('response lost'))
      .mockResolvedValueOnce({ message: message(), replayed: true, draft: remoteDraft });
    const { session, view } = await composer();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Committed message' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry send' })).toBeTruthy());
    // The websocket can report the committed atomic clear before HTTP retry finishes.
    view.rerender(<Composer session={session} room={{ ...session.getSnapshot().room!, draft: remoteDraft }}
      reply={null} editing={null} onClear={vi.fn()} registerGuard={vi.fn()} />);
    expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe('Committed message');
    fireEvent.click(screen.getByRole('button', { name: 'Retry send' }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(2));
    expect(send.mock.calls[0][1]).toEqual(send.mock.calls[1][1]);
    expect(send.mock.calls[0][1].draft_version).toBe(1);
    await waitFor(() => expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(remoteBody));
    expect(chatApi.saveDraft).toHaveBeenCalledTimes(1);
  });
});
describe('delegated group management', () => {
  it('a limited admin edits information without acquiring owner/settings rights', async () => {
    render(<GroupDialog conversation={{ ...conversation, caller: { group_role: 'admin', actions: ['manage_info'] } }} ownRole="owner" onClose={vi.fn()} onSaved={vi.fn()} />, { wrapper });
    expect((screen.getByRole('textbox', { name: 'Group name' }) as HTMLInputElement).disabled).toBe(false);
    expect((screen.getByRole('radio', { name: 'All members' }) as HTMLInputElement).closest('fieldset')?.disabled).toBe(true);
    expect(screen.queryByRole('button', { name: 'Administrators' })).toBeNull(); expect(screen.queryByRole('button', { name: 'Transfer ownership' })).toBeNull();
  });
  it('retains edits after a stale group version and offers an explicit reload', async () => {
    const save = vi.spyOn(chatApi, 'update').mockRejectedValue(new ApiError(409, 'changed', 'version_conflict'));
    render(<GroupDialog conversation={{ ...conversation, caller: { group_role: 'admin', actions: ['manage_info'] } }} ownRole="fleet" onClose={vi.fn()} onSaved={vi.fn()} />, { wrapper });
    fireEvent.change(screen.getByRole('textbox', { name: 'Group name' }), { target: { value: 'My title' } });
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Save' })));
    expect((screen.getByRole('textbox', { name: 'Group name' }) as HTMLInputElement).value).toBe('My title');
    expect(screen.getByRole('button', { name: 'Close and reload settings' })).toBeTruthy(); expect(save).toHaveBeenCalledTimes(1);
    expect((screen.getByRole('button', { name: 'Save' }) as HTMLButtonElement).disabled).toBe(true);
  });
});
