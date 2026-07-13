/**
 * VendorPicker — registry-first typeahead for the work-order form.
 *
 * Same interaction contract as the maintenance VehiclePicker: type to
 * filter, pick to select, or free-type a brand-new name (the WO save
 * path resolves-or-creates the registry row server-side, so an
 * unpicked name still links).  Picking a vendor hands the full row to
 * the parent so it can auto-fill the snapshot phone/address fields.
 */
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiJSON } from '../../api/client';
import type { Vendor } from '../../types';

export function VendorPicker({
  value,
  onChange,
}: {
  value: string;
  /** ``vendor`` is null while free-typing (no registry pick). */
  onChange: (name: string, vendor: Vendor | null) => void;
}) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const { data } = useQuery<{ vendors: Vendor[] }>({
    queryKey: ['vendors'],
    queryFn: () => apiJSON<{ vendors: Vendor[] }>('/vendors'),
    staleTime: 60_000,
  });
  const vendors = data?.vendors ?? [];

  // Sync visible text when the stored value arrives after mount
  // (edit mode hydration) — same pattern as VehiclePicker.
  useEffect(() => { setQuery(value); }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = query.trim()
    ? vendors.filter(v => v.name.toLowerCase().includes(query.toLowerCase()))
    : vendors;

  return (
    <div ref={ref} className="relative">
      <input
        type="text"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          onChange(e.target.value, null);
        }}
        onFocus={() => setOpen(true)}
        placeholder="Search or type a vendor…"
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-20 mt-1 w-full max-h-56 overflow-y-auto bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-1">
          {filtered.slice(0, 30).map(v => (
            <li key={v.id}>
              <button
                type="button"
                onClick={() => {
                  setQuery(v.name);
                  setOpen(false);
                  onChange(v.name, v);
                }}
                className="w-full text-left px-3 py-1.5 text-sm hover:bg-accent flex items-center justify-between gap-2"
              >
                <span className="truncate">{v.name}</span>
                {(v.work_order_count ?? 0) > 0 && (
                  <span className="text-2xs text-muted-foreground tabular-nums shrink-0">
                    {v.work_order_count} WO{(v.work_order_count ?? 0) === 1 ? '' : 's'}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
