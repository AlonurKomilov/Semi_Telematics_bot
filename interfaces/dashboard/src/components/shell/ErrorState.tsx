import { AlertTriangle } from 'lucide-react';

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
  className?: string;
}

export default function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
  className = '',
}: ErrorStateProps) {
  return (
    <div
      className={`bg-card border border-destructive/40 rounded-xl p-6 ${className}`}
    >
      <div className="flex items-start gap-3">
        <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-destructive/10 text-destructive shrink-0">
          <AlertTriangle size={18} />
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-foreground">{title}</p>
          {message && (
            <p className="text-sm text-muted-foreground mt-1 break-words">
              {message}
            </p>
          )}
          {onRetry && (
            <button
              onClick={onRetry}
              className="mt-3 px-3 py-1.5 text-xs font-medium rounded-md border border-border bg-background hover:bg-muted transition"
            >
              Try again
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
