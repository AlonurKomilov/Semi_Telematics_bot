/**
 * RangeTabs — the console's ONE time-window selector (chip row).
 *
 * Console-local single source of truth, mirroring the customer
 * dashboard's DateRangePresets idea without importing it (the two
 * apps are separate design languages by intent — design.md §9).
 * Same contract: one generic value in, onChange out.  Pages own what
 * the value means (days, a window key, a data tier).
 */

export interface RangeTab<T extends string | number> {
  value: T;
  label: string;
}

export default function RangeTabs<T extends string | number>({
  tabs, value, onChange,
}: {
  tabs: readonly RangeTab<T>[] | readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  const items: RangeTab<T>[] = tabs.map((t) =>
    typeof t === 'object' ? t : { value: t, label: String(t) },
  );
  return (
    <div className="flex gap-1">
      {items.map((t) => (
        <button
          key={String(t.value)}
          onClick={() => onChange(t.value)}
          className={`px-2 py-1 rounded text-[11px] border ${
            value === t.value
              ? 'bg-slate-700 border-slate-600 text-slate-100'
              : 'bg-slate-900 border-slate-800 text-slate-400 hover:text-slate-200'
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
