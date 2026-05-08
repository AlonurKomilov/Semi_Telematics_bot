import type { ReactNode } from 'react';

interface FilterChipsProps<T extends string> {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
  labelFor?: (v: T) => string;
  countFor?: (v: T) => number | undefined;
}

export function FilterChips<T extends string>({
  options,
  value,
  onChange,
  labelFor,
  countFor,
}: FilterChipsProps<T>) {
  return (
    <div className="flex flex-wrap gap-1.5">
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
