import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { ActionMenu } from '@/components/ui/context-menu';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog';
import { ErrorState, EmptyState } from '@/components/shell';
import { useScrollRegion } from '@/components/scrolling';
import { ArrowLeft, ArrowDown, MoreVertical, Search, Pin, Info } from '@/lib/icons';
import { Composer } from './Composer';
import { MessageView } from './MessageView';
import { PeoplePicker } from './PeoplePicker';
import { GroupDialog } from './GroupDialog';
import { SearchDialog } from './SearchDialog';
import { chatApi, errorKey } from './api';
import { canMarkRead, mergeMessages } from './model';
import { messageActions, conversationActions, type MessageAction } from './contextMenu';
import type { ChatSession, Room } from './session';
import type { Action, Message, PersonalState } from './types';
// Private draft/read/inbox updates must not remount every message menu.
// Primitive action flags keep memoization effective even when live caller arrays change.
const ThreadMessage = memo(function ThreadMessage({ message, own, pinned, timeZone, canSend, canPin, canModerate, onQuote, onAction }: {
  message: Message; own: boolean; pinned: boolean; timeZone?: string;
  canSend: boolean; canPin: boolean; canModerate: boolean;
  onQuote: (id: number) => void; onAction: (action: MessageAction, message: Message, pinned: boolean) => void;
}) {
  const { t } = useTranslation();
  const grants: Action[] = [...(canSend ? ['send' as const] : []), ...(canPin ? ['pin_messages' as const] : []), ...(canModerate ? ['delete_messages' as const] : [])];
  return <MessageView message={message} own={own} pinned={pinned} timeZone={timeZone} onQuote={onQuote}
    actions={messageActions(t, grants, message, pinned, action => onAction(action, message, pinned))} />;
});

export function Thread({ session, room, ownId, ownRole, name, onBack, registerGuard, target, onTarget, timeZone }: {
  session: ChatSession; room: Room; ownId: number; ownRole: string; name: string; onBack: () => void;
  timeZone?: string; registerGuard: (guard: (() => Promise<void>) | null) => void; target: number | null; onTarget: (id: number | null) => void;
}) {
  const { t } = useTranslation(); const cid = room.conversation.id;
  const [reply, setReply] = useState<Message | null>(null), [editing, setEditing] = useState<Message | null>(null);
  const [info, setInfo] = useState(false);
  const [manage, setManage] = useState(false), [search, setSearch] = useState<'search' | 'pins' | null>(null);
  const [remove, setRemove] = useState<Message | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [focused, setFocused] = useState<Message | null>(null), [targetLoading, setTargetLoading] = useState(false);
  const anchor = useRef<{ id: string; offset: number } | null>(null);
  const [bottom, setBottom] = useState(true); const keepBottom = useRef(true); const readThrough = useRef(room.state.last_read_message_seq);
  const region = useScrollRegion({ label: t('chat.messages') });
  const lastSeq = room.messages[room.messages.length - 1]?.message_seq ?? 0;
  const latest = () => { onTarget(null); keepBottom.current = true; setBottom(true); void session.history(true); };
  const composerGuard = useRef<(() => Promise<void>) | null>(null);
  const registerComposerGuard = useCallback((guard: (() => Promise<void>) | null) => { composerGuard.current = guard; registerGuard(guard); }, [registerGuard]);
  const clear = useCallback(() => { setReply(null); setEditing(null); }, []);
  useEffect(() => { if (!room.conversation.caller.actions.some(a => a.startsWith('manage_') || a === 'archive_group')) setManage(false); }, [room.conversation.caller.actions]);
  useEffect(() => {
    if (!target) { setFocused(null); return; }
    const controller = new AbortController(); setFocused(null); setTargetLoading(true); setError('');
    void chatApi.message(cid, target, controller.signal).then(message => { if (!controller.signal.aborted) { setFocused(message); setTargetLoading(false); } }).catch(e => { if (!controller.signal.aborted) { setError(errorKey(e)); setTargetLoading(false); } });
    return () => controller.abort();
  }, [cid, target, room.cursor]);
  // Visible quotes/edit buffers cannot retain removed text after a live tombstone.
  useEffect(() => {
    const rows = [...room.messages, ...room.pins];
    if (reply) { const current = rows.find(m => m.id === reply.id); if (current && current.version > reply.version) setReply(current.deleted_at ? null : current); }
    if (editing) { const current = rows.find(m => m.id === editing.id); if (current?.deleted_at) { setEditing(null); setError('conflict'); } }
  }, [room.messages, room.pins, reply, editing]);
  useLayoutEffect(() => {
    if (!room.browsingHistory && !target && keepBottom.current && region.node) region.node.scrollTop = region.node.scrollHeight;
  }, [room.messages, room.browsingHistory, region.node, target]);
  useLayoutEffect(() => {
    if (!anchor.current || !region.node) return;
    const element = region.node.querySelector(`[data-message-id="${anchor.current.id}"]`);
    if (element) region.node.scrollTop += element.getBoundingClientRect().top - region.node.getBoundingClientRect().top - anchor.current.offset;
    anchor.current = null;
  }, [room.messages, region.node]);
  const older = () => {
    if (region.node) {
      const top = region.node.getBoundingClientRect().top;
      const first = [...region.node.querySelectorAll<HTMLElement>('[data-message-id]')].find(el => el.getBoundingClientRect().bottom > top);
      if (first) anchor.current = { id: first.dataset.messageId!, offset: first.getBoundingClientRect().top - top };
    }
    keepBottom.current = false; setBottom(false); void session.history();
  };
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const mark = () => {
      clearTimeout(timer);
      if (!canMarkRead(!document.hidden, document.hasFocus(), bottom, room.browsingHistory || !!target) || lastSeq <= readThrough.current) return;
      timer = setTimeout(() => {
        if (!canMarkRead(!document.hidden, document.hasFocus(), keepBottom.current, room.browsingHistory || !!target)) return;
        void session.action(signal => chatApi.read(cid, lastSeq, signal), state => ({ state })).then(() => { readThrough.current = Math.max(readThrough.current, lastSeq); }).catch(() => { /* read receipts are best-effort; the next scroll, focus or visibility change marks again */ });
      }, 700);
    };
    mark(); window.addEventListener('focus', mark); document.addEventListener('visibilitychange', mark);
    return () => { clearTimeout(timer); window.removeEventListener('focus', mark); document.removeEventListener('visibilitychange', mark); };
  }, [session, cid, lastSeq, bottom, room.browsingHistory, target]);
  async function personal(key: 'muted' | 'pinned' | 'archived') {
    try { await session.action(signal => chatApi.setState(cid, { [key]: !room.state[key] }, signal), (state: PersonalState) => ({ state })); }
    catch (e) { setError(errorKey(e)); }
  }
  const menu = conversationActions(t, room.conversation, room.state, key => void personal(key), () => setManage(true));
  const act = useCallback(async (action: MessageAction, message: Message, pinned: boolean) => {
    if (action === 'reply' || action === 'edit') {
      try { await composerGuard.current?.(); } catch { setError('finishEditing'); return; }
    }
    if (action === 'reply') { setEditing(null); setReply(message); return; }
    if (action === 'edit') { setReply(null); setEditing(message); return; }
    if (action === 'delete') { setRemove(message); return; }
    try {
      await session.action(signal => chatApi.pin(cid, message, !pinned, signal));
      await session.action(signal => chatApi.pins(cid, undefined, signal), page => ({ pins: page.items }));
    } catch (e) { setError(errorKey(e)); }
  }, [session, cid]);
  async function deleteMessage() {
    if (!remove || busy) return; setBusy(true);
    try { await session.action(signal => chatApi.remove(cid, remove, signal), (message, current) => ({ messages: mergeMessages(current.messages, [message], current.scope.visible_after_message_seq), pins: current.pins.filter(p => p.id !== message.id) })); setRemove(null); }
    catch (e) { setError(errorKey(e)); setRemove(null); } finally { setBusy(false); }
  }
  const displayed = target ? focused ? [focused] : [] : room.messages;
  return <section className="flex h-full min-h-0 min-w-0 flex-1 flex-col text-foreground" aria-label={name}>
    <header className="flex flex-wrap shrink-0 items-center gap-2 border-b border-border p-3">
      <Button size="icon-sm" variant="ghost" className="text-foreground md:hidden" aria-label={t('chat.back')} onClick={onBack}><ArrowLeft /></Button>
      <div className="min-w-24 flex-1"><h2 className="truncate text-base font-semibold text-foreground">{name}</h2><p className="truncate text-xs text-muted-foreground">{room.conversation.description || t(`chat.${room.conversation.kind}`)}</p></div>
      <div className="flex shrink-0 items-center gap-2">
      <Button className="text-foreground" size="icon-sm" variant="ghost" aria-label={t('chat.info')} onClick={() => setInfo(true)}><Info /></Button>
      <Button className="text-foreground" size="icon-sm" variant="ghost" aria-label={t('chat.search')} onClick={() => setSearch('search')}><Search /></Button>
      <Button className="text-foreground" size="icon-sm" variant="ghost" aria-label={t('chat.pins')} onClick={() => setSearch('pins')}><Pin /></Button>
      <ActionMenu items={menu}><Button className="text-foreground" size="icon-sm" variant="ghost" aria-label={t('chat.chatActions')}><MoreVertical /></Button></ActionMenu>
      </div>
    </header>
    {error && <div className="px-3 pt-3"><ErrorState message={t(`chat.${error}`)} onRetry={() => { setError(''); void session.refreshRoom(); }} /></div>}
    <div ref={region.ref} {...region.props} className="min-h-0 flex-1 p-3" onScroll={event => { const el = event.currentTarget; const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight <= 2; keepBottom.current = atBottom; setBottom(atBottom); }}>
      {!target && room.before !== null && <div className="text-center"><Button className="text-foreground" size="sm" variant="outline" onClick={older}>{t('chat.older')}</Button></div>}
      {targetLoading && <p role="status" className="text-sm text-muted-foreground">{t('chat.loading')}</p>}
      {!targetLoading && !displayed.length && !target && <EmptyState title={t('chat.emptyThread')} description={t('chat.emptyThreadHint')} />}
      {displayed.map(message => <ThreadMessage key={message.id} timeZone={timeZone} message={message} own={message.author.id === ownId} pinned={room.pins.some(p => p.id === message.id)} onQuote={onTarget} onAction={act}
        canSend={room.conversation.caller.actions.includes('send')} canPin={room.conversation.caller.actions.includes('pin_messages')} canModerate={room.conversation.caller.actions.includes('delete_messages')} />)}
    </div>
    {(!bottom || room.browsingHistory || !!target) && <div className="shrink-0 px-3 pb-2 text-center"><Button size="sm" variant="secondary" onClick={latest}><ArrowDown />{t('chat.latest')}</Button></div>}
    <Composer session={session} room={room} reply={reply} editing={editing} onClear={clear} registerGuard={registerComposerGuard} />
    {info && <Dialog open onOpenChange={setInfo}><DialogContent size="lg"><DialogHeader><DialogTitle>{name}</DialogTitle><DialogDescription>{room.conversation.description || t(`chat.${room.conversation.kind}`)}</DialogDescription></DialogHeader><h3 className="text-base font-semibold text-foreground">{t('chat.members')}</h3><PeoplePicker key={room.conversation.version} conversationId={cid} members /></DialogContent></Dialog>}
    {manage && <GroupDialog conversation={room.conversation} ownRole={ownRole} onClose={() => { setManage(false); void session.refreshRoom(); }} onSaved={() => { void session.refreshRoom(); }} />}
    {search && <SearchDialog timeZone={timeZone} cid={cid} pins={search === 'pins'} revision={room.cursor} ownId={ownId} onClose={() => setSearch(null)} onOpen={id => { setSearch(null); onTarget(id); }} />}
    {remove && <Dialog open onOpenChange={open => { if (!open && !busy) setRemove(null); }}><DialogContent><DialogHeader><DialogTitle>{t('chat.delete')}</DialogTitle><DialogDescription>{t('chat.deleteWarning')}</DialogDescription></DialogHeader><DialogFooter><Button className="text-foreground" variant="outline" disabled={busy} onClick={() => setRemove(null)}>{t('chat.cancel')}</Button><Button variant="destructive" disabled={busy} onClick={() => void deleteMessage()}>{t('chat.delete')}</Button></DialogFooter></DialogContent></Dialog>}
  </section>;
}
