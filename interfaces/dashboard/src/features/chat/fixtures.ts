import { vi } from 'vitest';
import type { ChatApi } from './api';
import type { Batch, Conversation, Draft, Message, PersonalState } from './types';
export const conversation: Conversation = { id: 'room-a', kind: 'account', title: 'General', description: '', owner_user_id: 1, role_keys: [], version: 1, archived: false, system_key: 'general', settings: { posting_mode: 'everyone', new_member_history: 'all' }, caller: { group_role: 'owner', actions: ['send', 'manage_info', 'manage_settings', 'manage_admins', 'pin_messages', 'delete_messages', 'transfer_ownership'] } };
export const state: PersonalState = { muted: false, pinned: false, archived: false, last_read_message_seq: 0, last_seen_message_seq: 10 };
export const draft: Draft = { body: '', version: 0, reply: null };
export function message(id = 1, changes: Partial<Message> = {}): Message { return { id, message_seq: id, version: 1, author: { id: 2, display_name: 'Colleague' }, body: `Message ${id}`, created_at: '2026-09-16T09:00:00Z', edited_at: null, deleted_at: null, mentions: [], reply: null, can_edit: false, can_delete: true, ...changes }; }
export function batch(changes: Partial<Batch> = {}): Batch { return { conversation_id: 'room-a', items: [], cursor: 0, has_more: false, resync_required: false, scope: { membership_id: 1, visible_after_message_seq: 0 }, caller: conversation.caller, ...changes }; }
export function fakeApi(overrides: Partial<ChatApi> = {}): ChatApi {
  return { bootstrap: vi.fn(async () => ({ items: [{ ...conversation, state, unread_count: 1, unread_mentions: 0, has_draft: false, latest_message: message() }], next_after: null })), sync: vi.fn(async () => ({ revision: 1 })), conversation: vi.fn(async id => ({ ...conversation, id })),
    events: vi.fn(async (_id, after) => batch({ resync_required: after === undefined })), messages: vi.fn(async () => ({ items: [message()], next_before: null })), pins: vi.fn(async () => ({ items: [], next_before: null })), state: vi.fn(async () => state), draft: vi.fn(async () => draft), ...overrides } as ChatApi;
}
export function deferred<T>() { let resolve!: (value: T) => void, reject!: (error: unknown) => void; const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; }); return { promise, resolve, reject }; }
