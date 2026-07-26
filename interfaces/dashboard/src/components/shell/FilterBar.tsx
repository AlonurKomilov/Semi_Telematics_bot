import type { ReactNode } from 'react';

interface FilterChipsProps<T extends string> {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
  labelFor?: (v: T) => string;
  countFor?: (v: T) => number | undefined;
  /** What this group filters BY ("Status", "Type", "Severity").
   *
   *  Required in spirit whenever two chip groups sit next to each other:
   *  each exclusive group carries its own "All" option, so two unlabelled
   *  groups read as "All … All …" — one confused selection rather than two
   *  independent dimensions.  Also names the group for screen readers. */
  label?: string;
}

export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  labelFor,
  countFor,
  label,
}: FilterChipsProps<T>) {
  return (
    // role="group" only when it has a NAME — an unnamed group adds a
    // landmark screen readers can't describe.
    <div className="flex flex-wrap items-center gap-1.5"
         {...(label ? { role: 'group', 'aria-label': label } : {})}>
      {label && (
        <span className="text-2xs font-medium uppercase tracking-wide text-muted-foreground mr-0.5">
          {label}
        </span>
      )}
      {options.map((opt) => {
        const active = opt === value;
        const label = labelFor ? labelFor(opt) : opt;
        const count = countFor ? countFor(opt) : undefined;
        return (
          <button
            key={opt}
            onClick={() => onChange(opt)}
            className={`px-3 py-1.5 rounded-md text-xs font-medium capitalize transition border ${
              active
                ? 'bg-primary text-primary-foreground border-primary'
                : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
            }`}
          >
            {label}
            {count !== undefined && (
              <span
                className={`ml-1.5 ${active ? 'text-primary-foreground/80' : 'text-muted-foreground/70'}`}
              >
                {count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

interface FilterBarProps {
  children: ReactNode;
  className?: string;
}

export default function FilterBar({ children, className = '' }: FilterBarProps) {
  return (
    <div
      className={`flex flex-wrap items-center gap-3 mb-4 p-3 bg-card/50 border border-border rounded-lg ${className}`}
    >
      {children}
    </div>
  );
}
