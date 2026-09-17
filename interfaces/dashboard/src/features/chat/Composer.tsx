import { useEffect, useReducer, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { UserPlus, Send, X } from '@/lib/icons';
import { toneText } from '@/lib/status';
import { DraftBuffer } from './draft';
import { chatApi, errorKey } from './api';
import { sendPayload } from './model';
import { PeoplePicker } from './PeoplePicker';
import type { ChatSession, Room } from './session';
import type { Message, Person, SendPayload } from './types';
export function Composer({ session, room, reply, editing, onClear, registerGuard }: {
  session: ChatSession; room: Room; reply: Message | null; editing: Message | null; onClear: () => void;
  registerGuard: (guard: (() => Promise<void>) | null) => void;
}) {
  const { t } = useTranslation(); const [, render] = useReducer(n => n + 1, 0); const mounted = useRef(true);
  const cid = room.conversation.id;
  const [buffer] = useState(() => new DraftBuffer(room.draft, value => chatApi.saveDraft(cid, value), () => { if (mounted.current) render(); }));
  const [editBody, setEditBody] = useState(editing?.body ?? ''), [mentions, setMentions] = useState<Person[]>([]);
  const [mentionIds, setMentionIds] = useState<number[]>(editing?.mentions ?? []);
  const [dismissedReplyVersion, setDismissedReplyVersion] = useState<number | null>(null);
  const [pending, setPending] = useState<SendPayload | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState(''), [picking, setPicking] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(); const sending = useRef(false);
  const input = useRef<HTMLDivElement>(null);
  const allowed = room.conversation.caller.actions.includes('send');
  const unavailableReply = !editing && (room.messages.some(m => m.id === buffer.value.reply_to_id && !!m.deleted_at) || (!reply && room.draft.reply?.status === 'unavailable' && dismissedReplyVersion !== room.draft.version));
  const flush = () => buffer.flush();
  useEffect(() => { buffer.receive(room.draft); }, [buffer, room.draft]);
  useEffect(() => {
    if (!reply) return;
    buffer.update({ ...buffer.value, reply_to_id: reply.id }); input.current?.querySelector('textarea')?.focus();
  }, [buffer, reply]);
  useEffect(() => { setEditBody(editing?.body ?? ''); setMentionIds(editing?.mentions ?? []); setMentions([]); if (editing) input.current?.querySelector('textarea')?.focus(); }, [editing]);
  useEffect(() => {
    if (buffer.status !== 'unsaved' || !allowed) return;
    timer.current = setTimeout(() => { void buffer.flush().catch(() => { /* the buffer records the failure in its status: 'error' shows Retry below, 'conflict' its own panel */ }); }, 500);
    return () => clearTimeout(timer.current);
  }, [buffer, buffer.value, buffer.status, allowed]);
  useEffect(() => {
    registerGuard(async () => {
      if (sending.current || pending || editing) throw new Error('finishEditing');
      await flush();
    });
    return () => registerGuard(null);
  });
  useEffect(() => {
    mounted.current = true;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (buffer.status !== 'saved' || sending.current) { event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => {
      mounted.current = false; clearTimeout(timer.current); window.removeEventListener('beforeunload', beforeUnload);
      // Start with this room's identity while the old screen still owns the draft.
      // CAS prevents a late save from undoing another device's clear.
      if (session.isCurrent(cid, room.epoch) && buffer.status === 'unsaved') void buffer.flush().catch(() => { /* the screen is going away; the buffer keeps the failure in its status, nothing else could show it */ });
    };
  }, [buffer, session, cid, room.epoch]);
  async function send() {
    const body = editing ? editBody : pending?.body ?? buffer.value.body;
    if (!allowed || unavailableReply || busy || sending.current || !body.trim() || [...body.normalize('NFC').trim()].length > 4000) return;
    sending.current = true; setBusy(true); setError(''); clearTimeout(timer.current);
    try {
      if (editing) {
        const message = await session.action(signal => chatApi.edit(cid, editing, editBody, mentionIds, signal));
        session.addMessage(message); onClear();
      } else {
        await buffer.flush();
        const payload = pending ?? { ...sendPayload(body, buffer.value.reply_to_id, mentionIds), draft_version: buffer.version };
        setPending(payload);
        const { message, draft: consumedDraft } = await session.action(signal => chatApi.send(cid, payload, signal));
        session.addMessage(message);
        if (!mounted.current) return;
        setPending(null); setMentions([]); setMentionIds([]); onClear();
        if (consumedDraft) buffer.receive(consumedDraft);
        else {
          // Compatibility with an older API worker during a rolling deployment.
          buffer.update({ body: '', reply_to_id: null });
          await buffer.flush().catch(() => { /* the message is already sent; a stale draft is the lesser harm, and the buffer's status shows Retry */ });
        }
      }
    } catch (e) { if (mounted.current && !(e instanceof DOMException && e.name === 'AbortError')) setError(errorKey(e)); }
    finally { sending.current = false; if (mounted.current) { setBusy(false); input.current?.querySelector('textarea')?.focus(); } }
  }
  async function loadRemote() {
    try { const draft = await session.action(signal => chatApi.draft(cid, signal)); buffer.useRemote(draft); onClear(); setPending(null); setError(''); setMentions([]); setMentionIds([]); }
    catch (e) { if (mounted.current) setError(errorKey(e)); }
  }
  const body = editing ? editBody : pending?.body ?? buffer.value.body;
  const quote = unavailableReply ? t('chat.quoteUnavailable') : reply?.body ?? (room.draft.reply?.status === 'available' && room.draft.reply.id === buffer.value.reply_to_id ? room.draft.reply.body : t('chat.reply'));
  // No ground of its own: the panel above already paints `--card`, so
  // `bg-card` here was the same colour twice — and a SURFACE tone on a
  // plain element, which the material axis cannot reach. Under glass it
  // stayed a solid bar across a pane that had gone translucent.
  return <div ref={input} className="shrink-0 border-t border-border p-3 space-y-2 text-card-foreground">
    {(editing || buffer.value.reply_to_id !== null || unavailableReply) && <div className="flex items-center gap-2 border-l-2 border-primary pl-3 text-sm"><div className="min-w-0 flex-1"><p className="font-medium text-foreground">{t(editing ? 'chat.edit' : 'chat.reply')}</p><p className="truncate text-muted-foreground">{editing?.body ?? quote}</p></div><Button className="text-foreground" size="icon-sm" variant="ghost" aria-label={t('chat.cancel')} disabled={busy || !!pending} onClick={() => { if (!editing) { buffer.update({ ...buffer.value, reply_to_id: null }); setDismissedReplyVersion(room.draft.version); } onClear(); }}><X /></Button></div>}
    {!allowed && <p role="status" className="text-sm text-muted-foreground">{t(room.conversation.archived ? 'chat.groupArchived' : room.conversation.kind === 'direct' ? 'chat.peerUnavailable' : 'chat.adminsOnly')}</p>}
    <Textarea aria-label={t('chat.message')} placeholder={t('chat.message')} rows={2} maxLength={8000} disabled={!allowed || busy || !!pending} value={body}
      onChange={e => { if (editing) setEditBody(e.target.value); else buffer.update({ ...buffer.value, body: e.target.value }); }}
      onBlur={() => { if (buffer.status === 'unsaved') void flush().catch(() => { /* the buffer records the failure in its status: 'error' shows Retry below, 'conflict' its own panel */ }); }}
      onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) { e.preventDefault(); void send(); } }} />
    {mentions.length > 0 && <div className="flex flex-wrap gap-1">{mentions.map(person => <Button key={person.id} size="xs" variant="secondary" disabled={busy || !!pending} onClick={() => { setMentions(old => old.filter(p => p.id !== person.id)); setMentionIds(old => old.filter(id => id !== person.id)); }}>{person.display_name}<X /></Button>)}</div>}
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div className="flex items-center gap-2"><Button className="text-foreground" size="icon-sm" variant="ghost" aria-label={t('chat.mention')} disabled={!allowed || busy || !!pending} onClick={() => setPicking(true)}><UserPlus /></Button><p role="status" className="text-xs text-muted-foreground">{t(editing ? 'chat.editing' : `chat.draft_${buffer.status}`)}</p></div>
      <div className="flex items-center gap-2"><span className="text-xs text-muted-foreground">{[...body.normalize('NFC')].length}/4000</span><Button disabled={!allowed || unavailableReply || busy || !body.trim() || [...body.normalize('NFC').trim()].length > 4000 || buffer.status === 'conflict'} onClick={() => void send()}><Send />{t(busy ? 'chat.sending' : pending ? 'chat.retrySend' : editing ? 'chat.save' : 'chat.send')}</Button></div>
    </div>
    {error && <p role="alert" className={`text-sm ${toneText('danger')}`}>{t(`chat.${error}`)}{pending && <> {t('chat.retryHint')}</>}</p>}
    {buffer.status === 'conflict' && <div className="space-y-2"><p role="alert" className={`text-sm ${toneText('warn')}`}>{t('chat.draftConflict')}</p><Button className="text-foreground" size="sm" variant="outline" onClick={() => void loadRemote()}>{t('chat.useRemote')}</Button></div>}
    {buffer.status === 'error' && <Button className="text-foreground" size="sm" variant="outline" onClick={() => void flush().catch(() => { /* this IS the retry; the buffer's status stays 'error' until a flush succeeds and says so itself */ })}>{t('chat.retryDraft')}</Button>}
    {picking && <Dialog open onOpenChange={setPicking}><DialogContent><DialogHeader><DialogTitle>{t('chat.mention')}</DialogTitle><DialogDescription>{t('chat.mentionHint')}</DialogDescription></DialogHeader><PeoplePicker conversationId={cid} messageId={editing?.id} exclude={mentionIds} onPick={person => {
      setMentions(old => [...old, person]); setMentionIds(old => [...old, person.id]);
      const next = `${body}${body && !body.endsWith(' ') ? ' ' : ''}@${person.display_name} `;
      if (editing) setEditBody(next); else buffer.update({ ...buffer.value, body: next }); setPicking(false); input.current?.querySelector('textarea')?.focus();
    }} /></DialogContent></Dialog>}
  </div>;
}
