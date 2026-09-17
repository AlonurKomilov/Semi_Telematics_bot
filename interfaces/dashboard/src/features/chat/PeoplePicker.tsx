import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ScrollRegion } from '@/components/scrolling';
import { ErrorState, EmptyState } from '@/components/shell';
import { chatApi, errorKey } from './api';
import type { Person } from './types';
export function PeoplePicker({ conversationId, messageId, exclude = [], members = false, onPick }: { conversationId?: string; messageId?: number; exclude?: number[]; members?: boolean; onPick?: (p: Person) => void }) {
  const { t } = useTranslation();
  const [q, setQ] = useState(''), [items, setItems] = useState<Person[]>([]), [next, setNext] = useState<number | null>(null);
  const [after, setAfter] = useState<number | undefined>(), [busy, setBusy] = useState(true), [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setBusy(true); setError('');
    const timer = setTimeout(() => { void (members ? chatApi.members(conversationId!, q, after, controller.signal) : chatApi.people(q, conversationId, after, messageId, controller.signal)).then(page => {
      if (controller.signal.aborted) return;
      setItems(previous => after === undefined ? page.items : [...previous, ...page.items]); setNext(page.next_after); setBusy(false);
    }).catch(e => { if (!controller.signal.aborted) { setError(errorKey(e)); setBusy(false); } }); }, q ? 300 : 0);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [q, conversationId, messageId, after, retry, members]);
  return <div className="space-y-3">
    <Input aria-label={t('chat.people')} placeholder={t('chat.people')} value={q} maxLength={200} onChange={e => { setQ(e.target.value); setAfter(undefined); setItems([]); }} />
    {error && <ErrorState message={t(`chat.${error}`)} onRetry={() => setRetry(n => n + 1)} />}
    <ScrollRegion label={t('chat.people')} className="max-h-64 space-y-1">
      {items.filter(p => !exclude.includes(p.id)).map(p => {
        const identity = <span className="min-w-0 break-words">{p.display_name}<span className="block text-xs text-muted-foreground">{t(`roles.${p.role}`, { defaultValue: p.role })}{p.group_role && ` · ${t(`chat.groupRole_${p.group_role}`)}`}</span></span>;
        return onPick ? <Button key={p.id} variant="ghost" className="text-foreground w-full justify-start h-auto whitespace-normal text-left" onClick={() => onPick(p)} disabled={busy}>{identity}</Button> : <div key={p.id} className="px-3 py-2 text-sm text-foreground">{identity}</div>;
      })}
      {!busy && !items.length && !error && <EmptyState title={t('chat.noPeople')} />}
    </ScrollRegion>
    {busy && <p role="status" className="text-sm text-muted-foreground">{t('chat.loading')}</p>}
    {next !== null && <Button className="text-foreground" variant="outline" disabled={busy} onClick={() => setAfter(next)}>{t('chat.more')}</Button>}
  </div>;
}
