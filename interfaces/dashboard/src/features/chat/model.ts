import type { Message, Scope, SendPayload } from './types';
/** Apply current snapshots, never journal resource_version. Also invalidate cached quotes. */
export function mergeMessages(current: Message[], incoming: Message[], boundary = 0, limit = 100): Message[] {
  const rows = new Map(current.map(m => [m.id, m]));
  for (const m of incoming) if (!rows.has(m.id) || rows.get(m.id)!.version <= m.version) rows.set(m.id, m);
  return [...rows.values()].filter(m => m.message_seq > boundary).sort((a, b) => a.message_seq - b.message_seq).slice(-limit).map(m => {
    if (m.reply?.status !== 'available') return m;
    const quoted = rows.get(m.reply.id);
    if (!quoted) return m;
    return { ...m, reply: quoted.deleted_at ? { status: 'unavailable' } : { ...m.reply, body: quoted.body! } };
  });
}
export const sameScope = (a: Scope | null, b: Scope) => a?.membership_id === b.membership_id && a.visible_after_message_seq === b.visible_after_message_seq;
export function sendPayload(body: string, reply: number | null, mentions: number[], key: string = crypto.randomUUID()): SendPayload {
  return { client_message_id: key, body: body.normalize('NFC').trim(), reply_to_id: reply, mentions: [...new Set(mentions)].sort((a, b) => a - b) };
}
export function canMarkRead(visible: boolean, focused: boolean, atBottom: boolean, browsingHistory: boolean) {
  return visible && focused && atBottom && !browsingHistory;
}
