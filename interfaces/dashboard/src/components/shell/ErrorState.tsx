import { AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import { cardVariants } from '@/components/ui/card';

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export default function ErrorState({
  title,
  message,
  onRetry,
  className = '',
}: ErrorStateProps) {
  const { t } = useTranslation();
  // Defaults come from the shared `common` namespace so the shell
  // primitive isn't the one English island in a 9-locale app; an
  // explicit `title` prop still wins.
  const resolvedTitle = title ?? t('common.error_generic');
  return (
    <div
      // Not a <Card> either: the destructive edge is the warning. Same
      // shared definition underneath it.
      className={cn(cardVariants({ padding: 'panel' }), 'border-destructive/40', className)}
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-destructive/10 text-destructive shrink-0">
          <AlertTriangle className="size-4.5" />
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground">{resolvedTitle}</p>
          {message && (
            <p className="text-sm text-muted-foreground mt-1 break-words">
              {message}
            </p>
          )}
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-3 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-background hover:bg-muted transition min-h-tap"
            >
              {t('common.try_again')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
