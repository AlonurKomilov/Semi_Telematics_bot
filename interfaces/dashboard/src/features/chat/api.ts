import { apiJSON, ApiError } from '@/api/client';
import type { Conversation, Summary, PersonalState, Message, MessagePage, Draft, Person, Batch, SendPayload, Admins, Action } from './types';
const root = '/chat';
const room = (id: string) => `${root}/conversations/${encodeURIComponent(id)}`;
function query(values: Record<string, string | number | undefined>) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined) params.set(key, String(value)); });
  return `?${params}`;
}
export const chatApi = {
  bootstrap: (after?: string, signal?: AbortSignal) => apiJSON<{ items: Summary[]; next_after: string | null }>(`${root}/bootstrap${query({ after })}`, { signal }),
  sync: (signal?: AbortSignal) => apiJSON<{ revision: number }>(`${root}/sync`, { signal }),
  conversation: (id: string, signal?: AbortSignal) => apiJSON<Conversation>(room(id), { signal }),
  events: (id: string, after?: number, signal?: AbortSignal) => apiJSON<Batch>(`${room(id)}/events${query({ after })}`, { signal }),
  messages: (id: string, before?: number, signal?: AbortSignal) => apiJSON<MessagePage>(`${room(id)}/messages${query({ before })}`, { signal }),
  message: (id: string, mid: number, signal?: AbortSignal) => apiJSON<Message>(`${room(id)}/messages/${mid}`, { signal }),
  search: (id: string, q: string, before?: number, signal?: AbortSignal) => apiJSON<MessagePage>(`${room(id)}/search${query({ q, before })}`, { signal }),
  pins: (id: string, before?: number, signal?: AbortSignal) => apiJSON<MessagePage>(`${room(id)}/pins${query({ before })}`, { signal }),
  state: (id: string, signal?: AbortSignal) => apiJSON<PersonalState>(`${room(id)}/state`, { signal }),
  setState: (id: string, body: Partial<Pick<PersonalState, 'muted' | 'pinned' | 'archived'>>, signal?: AbortSignal) => apiJSON<PersonalState>(`${room(id)}/state`, { method: 'PATCH', body, signal }),
  read: (id: string, through: number, signal?: AbortSignal) => apiJSON<PersonalState>(`${room(id)}/read`, { method: 'PUT', body: { through_message_seq: through }, signal }),
  draft: (id: string, signal?: AbortSignal) => apiJSON<Draft>(`${room(id)}/draft`, { signal }),
  saveDraft: (id: string, body: { body: string; reply_to_id: number | null; expected_version: number }, signal?: AbortSignal) => apiJSON<Draft>(`${room(id)}/draft`, { method: 'PUT', body, signal }),
  send: (id: string, body: SendPayload, signal?: AbortSignal) => apiJSON<{ message: Message; replayed: boolean; draft?: Draft }>(`${room(id)}/messages`, { method: 'POST', body: { ...body }, signal }),
  edit: (id: string, message: Message, body: string, mentions: number[], signal?: AbortSignal) => apiJSON<Message>(`${room(id)}/messages/${message.id}`, { method: 'PATCH', body: { body, mentions, expected_version: message.version }, signal }),
  remove: (id: string, message: Message, signal?: AbortSignal) => apiJSON<Message>(`${room(id)}/messages/${message.id}?expected_version=${message.version}`, { method: 'DELETE', signal }),
  pin: (id: string, message: Message, pinned: boolean, signal?: AbortSignal) => apiJSON(`${room(id)}/pins/${message.id}?expected_version=${message.version}`, { method: pinned ? 'PUT' : 'DELETE', signal }),
  members: (id: string, q: string, after?: number, signal?: AbortSignal) => apiJSON<{ items: Person[]; next_after: number | null }>(`${room(id)}/members${query({ q, after })}`, { signal }),
  people: (q: string, conversation_id?: string, after?: number, message_id?: number, signal?: AbortSignal) => apiJSON<{ items: Person[]; next_after: number | null }>(`${root}/people${query({ q, conversation_id, after, message_id })}`, { signal }),
  create: (body: { title: string; description: string; kind: 'account' | 'roles'; role_keys: string[]; posting_mode: string; new_member_history: string }) => apiJSON<Conversation>(`${root}/conversations`, { method: 'POST', body }),
  direct: (other_user_id: number) => apiJSON<Conversation>(`${root}/direct`, { method: 'POST', body: { other_user_id } }),
  update: (c: Conversation, changes: Record<string, unknown>) => apiJSON<Conversation>(room(c.id), { method: 'PATCH', body: { expected_version: c.version, changes } }),
  admins: (id: string, signal?: AbortSignal) => apiJSON<Admins>(`${room(id)}/admins`, { signal }),
  setAdmin: (id: string, version: number, uid: number, actions: Action[]) => apiJSON<Conversation>(`${room(id)}/admins/${uid}`, { method: 'PUT', body: { expected_version: version, actions } }),
  removeAdmin: (id: string, version: number, uid: number) => apiJSON<Conversation>(`${room(id)}/admins/${uid}?expected_version=${version}`, { method: 'DELETE' }),
  transfer: (c: Conversation, new_owner_id: number) => apiJSON<Conversation>(`${room(c.id)}/transfer-ownership`, { method: 'POST', body: { expected_version: c.version, new_owner_id } }),
};
export type ChatApi = typeof chatApi;
export function chatSocketURL(base = import.meta.env.VITE_API_BASE ?? '/api', origin = window.location.origin) {
  const url = new URL(`${base.replace(/\/$/, '')}/chat/ws`, origin);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  // Credentials belong exclusively to the normal dashboard cookie.
  url.search = ''; url.hash = ''; return url.href;
}
export function errorKey(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 409) return 'conflict';
    if (error.status === 429) return 'rateLimit';
    if ([401, 402, 403, 404].includes(error.status)) return 'unavailable';
    if ([400, 422].includes(error.status)) return 'invalid';
  }
  return 'connectionError';
}
export { ApiError };
