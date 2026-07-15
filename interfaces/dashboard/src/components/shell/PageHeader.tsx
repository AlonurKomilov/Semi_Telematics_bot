import { Link } from 'react-router-dom';
import { HelpCircle, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { InfoTip } from '../tooltip';

interface PageHeaderProps {
  title: string;
  description?: string;
  icon?: LucideIcon;
  actions?: ReactNode;
  meta?: ReactNode;
  /** Optional help link — internal route or absolute URL. */
  helpHref?: string;
  helpLabel?: string;
}

export default function PageHeader({
  title,
  description,
  icon: Icon,
  actions,
  meta,
  helpHref,
  helpLabel = 'Learn more',
}: PageHeaderProps) {
  const isExternal = helpHref?.startsWith('http');
  return (
    <div className="mb-6">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            {Icon && (
              <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-primary/10 text-primary shrink-0">
                <Icon size={18} />
              </span>
            )}
            <h1 className="text-2xl font-bold text-foreground truncate">{title}</h1>
            {/* Page description lives behind ⓘ — one hover away instead of a
                permanent line under every title (product decision: the
                title + content should orient; the sentence is reference).
                Converts all pages at once since every page uses this shell. */}
            {description && <InfoTip size={16} label={description} />}
          </div>
          {helpHref && (
            <p className="text-sm text-muted-foreground mt-1.5 max-w-2xl">
              {isExternal ? (
                <a
                  href={helpHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  <HelpCircle size={12} />
                  {helpLabel}
                </a>
              ) : (
                <Link
                  to={helpHref}
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  <HelpCircle size={12} />
                  {helpLabel}
                </Link>
              )}
            </p>
          )}
          {meta && <div className="mt-2">{meta}</div>}
        </div>
        {actions && (
          <div className="flex items-center gap-2 shrink-0">{actions}</div>
        )}
      </div>
    </div>
  );
}
