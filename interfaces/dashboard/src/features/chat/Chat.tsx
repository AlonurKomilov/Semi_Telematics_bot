import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useAuth } from '@/context/AuthContext';
import { useRoleView } from '@/context/RoleViewContext';
import { tierLabel } from '@/context/roleViewTarget';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { EmptyState, ErrorState, PageHeader, CardSkeleton } from '@/components/shell';
import { ScrollRegion } from '@/components/scrolling';
import { MessageSquare, Plus, Pin, BellOff, Users } from '@/lib/icons';
import { formatTime } from '@/utils/datetime';
import { cn } from '@/lib/utils';
import { GroupDialog } from './GroupDialog';
import { PeoplePicker } from './PeoplePicker';
import { Thread } from './Thread';
import { ChatSession } from './session';
import { chatApi, errorKey } from './api';
import type { Summary } from './types';
import type { User } from '@/types';
const conversationName = (c: Summary, directLabel: string) => c.kind === 'direct' ? c.peer?.display_name ?? directLabel : c.title;
/** Exported for the isolated browser/test harness; production identity only comes from AuthContext. */
export function ChatWorkspace({ session, user, accessHint, onRetryAccess }: { session: ChatSession; user: User; accessHint?: string; onRetryAccess?: () => void }) {
  const { t } = useTranslation(); const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const [params, setParams] = useSearchParams(); const selected = params.get('conversation'); const target = Number(params.get('message')) || null;
  const [filter, setFilter] = useState(''), [archived, setArchived] = useState(false), [create, setCreate] = useState<'choose' | 'group' | 'direct' | null>(null);
  const [listPage, setListPage] = useState(0);
  const [slice, setSlice] = useState<'all' | 'unreadChats' | 'mentions' | 'pinned'>('all');
  const [navError, setNavError] = useState(''), [busy, setBusy] = useState(false);
  useEffect(() => setListPage(0), [filter, archived, slice]);
  const guard = useRef<(() => Promise<void>) | null>(null);
  const registerGuard = useCallback((fn: (() => Promise<void>) | null) => { guard.current = fn; }, []);
  useEffect(() => {
    if (selected === state.selected) return;
    let cancelled = false;
    void (async () => {
      try { await guard.current?.(); if (!cancelled) { session.select(selected); setNavError(''); } }
      catch { if (!cancelled) { setNavError('finishEditing'); setParams(state.selected ? { conversation: state.selected } : {}, { replace: true }); } }
    })();
    return () => { cancelled = true; };
  }, [selected, state.selected, session, setParams]);
  const open = (id: string | null) => { if (id === state.selected && !state.room) session.select(id); setParams(id ? { conversation: id } : {}); };
  const rows = state.summaries.filter(c => c.state.archived === archived && (slice === 'all' || (slice === 'unreadChats' ? c.unread_count > 0 : slice === 'mentions' ? c.unread_mentions > 0 : c.state.pinned)) && conversationName(c, t('chat.direct')).toLocaleLowerCase().includes(filter.toLocaleLowerCase())).sort((a, b) =>
    Number(b.state.pinned) - Number(a.state.pinned) || Number(b.system_key === 'general') - Number(a.system_key === 'general') || Date.parse(b.latest_message?.created_at ?? '1970-01-01') - Date.parse(a.latest_message?.created_at ?? '1970-01-01'));
  const roomId = state.room?.conversation.id;
  const openMessage = useCallback((id: number | null) => {
    if (roomId) setParams({ conversation: roomId, ...(id ? { message: String(id) } : {}) });
  }, [roomId, setParams]);
  const summary = state.summaries.find(c => c.id === state.selected);
  const name = summary ? conversationName(summary, t('chat.direct')) : state.room?.conversation.title || t('chat.direct');
  if (state.accessDenied) return <div data-chat-page>
    <PageHeader title={t('nav.chat')} icon={MessageSquare} />
    <EmptyState icon={MessageSquare} title={t('chat.accessUnavailableTitle')}
      description={accessHint || t('chat.accessUnavailableHint')}
      action={onRetryAccess ? <Button onClick={onRetryAccess}>{t('chat.checkAccess')}</Button> : undefined} />
  </div>;
  return <div className="flex h-full min-h-0 flex-col text-foreground" data-chat-page>
    <div className={cn('shrink-0', selected && 'hidden md:block')}><PageHeader title={t('nav.chat')} icon={MessageSquare} actions={<Button onClick={() => setCreate('choose')}><Plus />{t('chat.newChat')}</Button>} /></div>
    {accessHint && <p className="shrink-0 mb-2 text-xs text-muted-foreground">{accessHint}</p>}
    <p role="status" className="shrink-0 mb-2 text-xs text-muted-foreground">{t(`chat.connection_${state.connection}`)}</p>
    {navError && <ErrorState message={t(`chat.${navError}`)} />}
    {state.error && <ErrorState message={t(`chat.${state.error}`)} onRetry={state.error === 'unavailable' ? undefined : () => { void session.refreshInbox(); session.select(selected); }} />}
    <Card padding="none" className="flex min-h-0 flex-1 text-card-foreground">
      <aside className={cn('flex w-full shrink-0 flex-col min-h-0 md:w-80 md:border-r md:border-border', selected && 'hidden md:flex')} aria-label={t('chat.conversations')}>
        <div className="shrink-0 border-b border-border p-3 space-y-2"><Input aria-label={t('chat.filter')} placeholder={t('chat.filter')} value={filter} onChange={e => setFilter(e.target.value)} /><div className="flex gap-2"><Button className="text-foreground" size="sm" variant={!archived ? 'secondary' : 'ghost'} aria-pressed={!archived} onClick={() => setArchived(false)}>{t('chat.inbox')}</Button><Button className="text-foreground" size="sm" variant={archived ? 'secondary' : 'ghost'} aria-pressed={archived} onClick={() => setArchived(true)}>{t('chat.archived')}</Button></div><div className="flex flex-wrap gap-1">{(['all', 'unreadChats', 'mentions', 'pinned'] as const).map(key => <Button key={key} size="xs" variant={slice === key ? 'secondary' : 'ghost'} className="text-foreground" aria-pressed={slice === key} onClick={() => setSlice(key)}>{t(`chat.filter_${key}`)}</Button>)}</div></div>
        <ScrollRegion label={t('chat.conversations')} className="min-h-0 flex-1 p-2 space-y-1">
          {rows.slice(listPage * 50, (listPage + 1) * 50).map(c => <Button key={c.id} variant="ghost" aria-current={state.selected === c.id ? 'true' : undefined} className={cn('text-foreground', 'w-full h-auto items-start justify-start whitespace-normal text-left p-3', state.selected === c.id && 'bg-accent text-accent-foreground')} onClick={() => open(c.id)}>
            <span className="min-w-0 flex-1"><span className="flex gap-2 items-center"><span className="truncate font-medium flex-1">{conversationName(c, t('chat.direct'))}</span>{c.state.pinned && <Pin aria-label={t('chat.pinned')} />}{c.state.muted && <BellOff aria-label={t('chat.muted')} />}{c.unread_count > 0 && <span className="rounded-full bg-primary text-primary-foreground px-2 py-0.5 text-xs" aria-label={t('chat.unread', { count: c.unread_count })}>{c.unread_count}</span>}</span><span className="block truncate text-xs text-muted-foreground mt-1">{c.has_draft ? `${t('chat.draftLabel')} · ` : ''}{c.latest_message?.body ?? t('chat.emptyThread')}</span><span className="flex justify-between gap-2 mt-1 text-xs text-muted-foreground">{c.latest_message && <time dateTime={c.latest_message.created_at}>{formatTime(c.latest_message.created_at, { timeZone: user.effective_timezone || user.timezone })}</time>}{c.unread_mentions > 0 && <span aria-label={t('chat.mentionCount', { count: c.unread_mentions })}>@{c.unread_mentions}</span>}</span></span>
          </Button>)}
          {!state.inboxLoading && !rows.length && <EmptyState title={t('chat.noConversations')} action={<Button className="text-foreground" variant="outline" onClick={() => setCreate('choose')}>{t('chat.newChat')}</Button>} />}
          {state.inboxLoading && <p role="status" className="p-3 text-sm text-muted-foreground">{t('chat.loading')}</p>}
          {(listPage > 0 || rows.length > (listPage + 1) * 50) && <div className="flex justify-between gap-2">{listPage > 0 && <Button variant="outline" className="text-foreground" onClick={() => setListPage(n => n - 1)}>{t('chat.previous')}</Button>}{rows.length > (listPage + 1) * 50 && <Button variant="outline" className="text-foreground" onClick={() => setListPage(n => n + 1)}>{t('chat.next')}</Button>}</div>}
          {state.next && <Button className="text-foreground w-full" variant="outline" disabled={state.inboxLoading} onClick={() => void session.refreshInbox(true)}>{t('chat.more')}</Button>}
        </ScrollRegion>
      </aside>
      <div className={cn('flex min-w-0 min-h-0 flex-1', !selected && 'hidden md:flex')}>
        {state.room && <Thread key={`${state.room.conversation.id}:${state.room.epoch}`} session={session} room={state.room} ownId={user.id!} ownRole={user.role} timeZone={user.effective_timezone || user.timezone} name={name} onBack={() => open(null)} registerGuard={registerGuard} target={target} onTarget={openMessage} />}
        {!state.room && <div className="flex-1 min-w-0 p-4">{state.loading ? <CardSkeleton message={t('chat.loading')} /> : <EmptyState icon={MessageSquare} title={t(state.error ? 'chat.unavailable' : 'chat.selectConversation')} description={t('chat.selectHint')} action={selected ? <Button className="text-foreground" variant="outline" onClick={() => open(null)}>{t('chat.back')}</Button> : undefined} />}</div>}
      </div>
    </Card>
    {(create === 'choose' || create === 'direct') && <Dialog open onOpenChange={openDialog => { if (!openDialog && !busy) setCreate(null); }}><DialogContent><DialogHeader><DialogTitle>{t('chat.newChat')}</DialogTitle><DialogDescription>{t(create === 'direct' ? 'chat.directHint' : 'chat.newHint')}</DialogDescription></DialogHeader>
      {create === 'choose' ? <div className="flex flex-col gap-3"><Button className="text-foreground" variant="outline" onClick={() => setCreate('group')}><Users />{t('chat.newGroup')}</Button><Button className="text-foreground" variant="outline" onClick={() => setCreate('direct')}><MessageSquare />{t('chat.direct')}</Button></div> : <fieldset disabled={busy}><PeoplePicker exclude={[user.id!]} onPick={p => {
        setBusy(true); void chatApi.direct(p.id).then(c => { setCreate(null); open(c.id); void session.refreshInbox(); }).catch(e => setNavError(errorKey(e))).finally(() => setBusy(false));
      }} /></fieldset>}
    </DialogContent></Dialog>}
    {create === 'group' && <GroupDialog ownRole={user.role} onClose={() => setCreate(null)} onSaved={c => { open(c.id); void session.refreshInbox(); }} />}
  </div>;
}
function AuthenticatedChat({ user }: { user: User }) {
  const { t } = useTranslation();
  const { refreshUser } = useAuth();
  const { isPreviewing, viewLabel, activeView, activePermissionKey, ownViewLabel } = useRoleView();
  const [session, setSession] = useState<ChatSession | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const next = new ChatSession(); setSession(next); next.start();
    return () => next.stop();
  }, [attempt, user.permissions?.can_view_chat]);
  const previewTier = tierLabel(activeView, activePermissionKey);
  const accessHint = isPreviewing ? t('chat.previewIdentity', {
    preview: `${viewLabel}${previewTier ? ` · ${previewTier}` : ''}`,
    identity: `${user.display_name || ''} · ${ownViewLabel}`,
  }) : undefined;
  return session ? <ChatWorkspace session={session} user={user} accessHint={accessHint}
    onRetryAccess={() => { void refreshUser().finally(() => setAttempt(n => n + 1)); }} />
    : <CardSkeleton message={t('chat.loading')} />;
}
export default function Chat() {
  const { user, loading } = useAuth();
  if (loading) return <CardSkeleton />;
  // The shared route guard owns view permissions, as for the other services.
  // The backend resolves this real member; a preview never supplies an actor.
  if (!user?.id || !user.account_id) return <Navigate to="/" replace />;
  return <AuthenticatedChat key={`${user.account_id}:${user.id}`} user={user} />;
}
