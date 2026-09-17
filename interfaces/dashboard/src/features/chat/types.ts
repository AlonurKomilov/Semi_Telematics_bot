/** Wire types from capabilities/chat/docs/API.md and REALTIME.md. */
export type Action = 'send' | 'manage_info' | 'manage_settings' | 'manage_audience' | 'pin_messages' | 'delete_messages' | 'archive_group' | 'manage_admins' | 'transfer_ownership';
export interface Caller { group_role: 'owner' | 'admin' | 'member'; actions: Action[]; visible_after_message_seq?: number }
export interface Conversation {
  id: string; kind: 'account' | 'roles' | 'direct'; title: string; description: string;
  owner_user_id: number | null; role_keys: string[]; version: number; archived: boolean; system_key: string | null;
  settings: { posting_mode: 'everyone' | 'admins'; new_member_history: 'all' | 'since_join' }; caller: Caller;
}
export interface PersonalState { muted: boolean; pinned: boolean; archived: boolean; last_read_message_seq: number; last_seen_message_seq: number }
export interface Summary extends Conversation { latest_message: Message | null; unread_count: number; unread_mentions: number; has_draft: boolean; state: PersonalState; peer?: { id: number; display_name: string; is_active: boolean } }
export type Quote = { status: 'unavailable' } | { status: 'available'; id: number; author_id: number; body: string };
export interface Message {
  id: number; message_seq: number; version: number; author: { id: number; display_name: string }; body: string | null;
  created_at: string; edited_at: string | null; deleted_at: string | null; mentions: number[]; reply: Quote | null;
  can_edit: boolean; can_delete: boolean; client_message_id?: string;
}
export interface Draft { body: string; version: number; reply: Quote | null }
export interface Person { id: number; display_name: string; role: string; group_role?: Caller['group_role'] }
export interface MessagePage { items: Message[]; next_before: number | null }
export interface Scope { membership_id: number; visible_after_message_seq: number }
export interface Batch {
  conversation_id: string; items: { id: number; event_seq: number; kind: string; resource_version: number; message?: Message }[];
  cursor: number; has_more: boolean; resync_required: boolean; scope: Scope; caller: Caller;
}
export interface SendPayload { client_message_id: string; draft_version?: number; body: string; reply_to_id: number | null; mentions: number[] }
export interface Admin { user_id: number; display_name: string; actions: Action[] }
export interface Admins { owner_user_id: number; version: number; items: Admin[] }
