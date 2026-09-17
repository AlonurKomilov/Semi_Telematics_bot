import { useTranslation } from 'react-i18next';
import { MoreVertical, Pin } from '@/lib/icons';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ActionMenu, ContextMenu, type MenuAction } from '@/components/ui/context-menu';
import { formatDate } from '@/utils/datetime';
import { cn } from '@/lib/utils';
import type { Message } from './types';
export function MessageView({ message, own, pinned = false, actions = [], onQuote, timeZone }: { message: Message; own: boolean; pinned?: boolean; actions?: MenuAction[]; timeZone?: string; onQuote?: (id: number) => void }) {
  const { t } = useTranslation();
  return <ContextMenu items={actions} render={<article data-message-id={message.id} />}>
    <div className={cn('flex gap-2 py-2', own && 'flex-row-reverse')}>
      <Card padding="compact" className={cn('min-w-0 max-w-xl flex-1', own ? 'bg-primary/10 text-foreground' : 'text-card-foreground')}>
        <div className="flex items-center justify-between gap-3 mb-1"><span className="text-xs font-semibold text-foreground break-words">{message.author.display_name}</span>{pinned && <Pin className="size-3 shrink-0 text-muted-foreground" aria-label={t('chat.pinned')} />}</div>
        {message.reply && <Button size="sm" variant="ghost" className="text-foreground mb-2 h-auto w-full justify-start whitespace-normal border-l-2 border-border text-left" disabled={message.reply.status !== 'available' || !onQuote} onClick={() => { if (message.reply?.status === 'available') onQuote?.(message.reply.id); }}>{message.reply.status === 'available' ? <span className="line-clamp-2 break-all">{message.reply.body}</span> : t('chat.quoteUnavailable')}</Button>}
        <p className={cn('whitespace-pre-wrap break-words text-sm [overflow-wrap:anywhere]', message.deleted_at && 'italic text-muted-foreground')}>{message.deleted_at ? t('chat.deleted') : message.body}</p>
        <div className="mt-2 flex flex-wrap justify-end gap-2 text-xs text-muted-foreground"><time dateTime={message.created_at}>{formatDate(message.created_at, { timeZone })}</time>{message.edited_at && !message.deleted_at && <span>{t('chat.edited')}</span>}</div>
      </Card>
      <ActionMenu items={actions}><Button size="icon-sm" variant="ghost" className="text-foreground shrink-0" aria-label={t('chat.messageActions')}><MoreVertical /></Button></ActionMenu>
    </div>
  </ContextMenu>;
}
