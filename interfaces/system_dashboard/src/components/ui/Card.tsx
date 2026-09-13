/** The console's card: a titled section with an optional action slot.
 *  Two pages had their own copy; they agreed on everything except which
 *  heading level to use, which is the kind of thing that only diverges
 *  once nobody can see both at the same time. */

import type { ReactNode } from 'react';

interface Props {
  title?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ title, actions, children, className = '' }: Props) {
  return (
    <section className={`bg-slate-900 border border-slate-800 rounded-lg p-4 ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between mb-3">
          {title && <h2 className="text-sm font-semibold text-slate-200">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}
