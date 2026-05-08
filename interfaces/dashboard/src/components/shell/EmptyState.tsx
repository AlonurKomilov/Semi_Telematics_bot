import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Inbox } from 'lucide-react';

interface EmptyStateProps {
  icon?: LucideIcon;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export default function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className = '',
}: EmptyStateProps) {
  return (
    <div
      className={`bg-card border border-dashed border-border rounded-xl p-10 text-center ${className}`}
    >
      <div className="inline-flex items-center justify-center w-12 h-12 rounded-full bg-muted text-muted-foreground mb-4">
        <Icon size={22} />
      </div>
      <p className="text-base font-medium text-foreground">{title}</p>
      {description && (
        <p className="text-sm text-muted-foreground mt-1.5 max-w-md mx-auto">
          {description}
        </p>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
