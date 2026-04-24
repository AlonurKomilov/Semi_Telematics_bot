import { Badge } from './ui/badge';
import { cn } from '../lib/utils';

const STATUS_CLASSES: Record<string, string> = {
  moving:   'bg-green-500/15  text-green-700  dark:text-green-400  border-green-500/30',
  active:   'bg-green-500/15  text-green-700  dark:text-green-400  border-green-500/30',
  On:       'bg-green-500/15  text-green-700  dark:text-green-400  border-green-500/30',
  idle:     'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30',
  Idle:     'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400 border-yellow-500/30',
  stopped:  'bg-red-500/15   text-red-600    dark:text-red-400    border-red-500/30',
  inactive: 'bg-red-500/15   text-red-600    dark:text-red-400    border-red-500/30',
  Off:      'bg-muted        text-muted-foreground border-border/50',
};

export default function StatusBadge({ status }: { status: string }) {
  const classes = STATUS_CLASSES[status] ?? 'bg-muted text-muted-foreground border-border/50';
  return (
    <Badge variant="outline" className={cn('text-xs font-medium', classes)}>
      {status}
    </Badge>
  );
}
