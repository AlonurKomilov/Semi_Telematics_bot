import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { ScrollRegion } from '@/components/scrolling';
import { EmptyState, ErrorState } from '@/components/shell';
import { chatApi, errorKey } from './api';
import { MessageView } from './MessageView';
import type { Message } from './types';
export function SearchDialog({ cid, pins, revision, ownId, onClose, onOpen, timeZone }: { cid: string; pins: boolean; revision: number; ownId: number; timeZone?: string; onClose: () => void; onOpen: (id: number) => void }) {
  const { t } = useTranslation(); const [q, setQ] = useState(''), [before, setBefore] = useState<number | undefined>();
  const [loadedRevision, setLoadedRevision] = useState<number | null>(null);
  const [items, setItems] = useState<Message[]>([]), [next, setNext] = useState<number | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  useEffect(() => { setBefore(undefined); setItems([]); }, [revision]);
  useEffect(() => {
    const controller = new AbortController(); setItems([]); setError(''); setNext(null);
    if (!pins && !q.trim()) return () => controller.abort();
    setBusy(true);
    const timer = setTimeout(() => { void (pins ? chatApi.pins(cid, before, controller.signal) : chatApi.search(cid, q.trim(), before, controller.signal)).then(page => {
      if (!controller.signal.aborted) { setItems(page.items); setLoadedRevision(revision); setNext(page.next_before); setBusy(false); }
    }).catch(e => { if (!controller.signal.aborted) { setError(errorKey(e)); setBusy(false); } }); }, pins ? 0 : 350);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [cid, pins, q, before, revision]);
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}><DialogContent size="lg"><DialogHeader><DialogTitle>{t(pins ? 'chat.pins' : 'chat.search')}</DialogTitle><DialogDescription>{t('chat.searchHint')}</DialogDescription></DialogHeader>
    {!pins && <Input autoFocus value={q} maxLength={200} aria-label={t('chat.search')} placeholder={t('chat.search')} onChange={e => { setQ(e.target.value); setBefore(undefined); }} />}
    {error && <ErrorState message={t(`chat.${error}`)} />}
    <ScrollRegion label={t('chat.results')} className="max-h-96 space-y-3">
      {(loadedRevision === revision ? items : []).map(message => <div key={message.id}><MessageView timeZone={timeZone} message={message} own={message.author.id === ownId} /><Button className="text-foreground" variant="outline" size="sm" onClick={() => onOpen(message.id)}>{t('chat.openMessage')}</Button></div>)}
      {!busy && !items.length && !error && (pins || q.trim()) && <EmptyState title={t('chat.noResults')} />}
    </ScrollRegion>
    {busy && <p role="status" className="text-sm text-muted-foreground">{t('chat.loading')}</p>}
    {next !== null && <Button className="text-foreground" variant="outline" disabled={busy} onClick={() => setBefore(next)}>{t('chat.older')}</Button>}
  </DialogContent></Dialog>;
}
