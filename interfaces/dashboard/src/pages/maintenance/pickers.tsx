import { useState, useEffect, useRef } from 'react';

export interface FleetVehicle {
  name: string;
  company: string;
  status: string;
  fuel_percent: number | null;
  speed_mph: number;
}

const STATUS_DOT: Record<string, string> = {
  moving: 'bg-green-500',
  idle: 'bg-yellow-400',
  stopped: 'bg-red-500',
};

export function VehiclePicker({
  value,
  onChange,
  vehicles,
  loading: fleetLoading,
}: {
  value: string;
  onChange: (name: string, vehicle: FleetVehicle | null) => void;
  vehicles: FleetVehicle[];
  loading: boolean;
}) {
  const [query, setQuery] = useState(value);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => { if (!value) setQuery(''); }, [value]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const filtered = query.trim()
    ? vehicles.filter(
        (v) =>
          v.name.toLowerCase().includes(query.toLowerCase()) ||
          v.company.toLowerCase().includes(query.toLowerCase()),
      )
    : vehicles;

  const select = (v: FleetVehicle) => {
    setQuery(v.name);
    setOpen(false);
    onChange(v.name, v);
  };

  return (
    <div ref={ref} className="relative">
      <input
        required
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          onChange(e.target.value, null);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder={fleetLoading ? 'Loading vehicles…' : 'Search truck or company…'}
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-72 max-h-64 overflow-y-auto bg-card border border-border rounded-lg shadow-xl text-sm">
          {filtered.map((v) => (
            <li
              key={v.name}
              onMouseDown={() => select(v)}
              className="flex items-center gap-2 px-3 py-2 hover:bg-muted cursor-pointer"
            >
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_DOT[v.status] || 'bg-gray-500'}`} />
              <span className="font-mono font-semibold flex-shrink-0">{v.name}</span>
              <span className="px-1.5 py-0.5 bg-muted rounded text-xs text-muted-foreground flex-shrink-0">{v.company}</span>
              <span className="text-xs text-muted-foreground capitalize flex-shrink-0">{v.status}</span>
              {v.fuel_percent != null && (
                <span className={`ml-auto text-xs flex-shrink-0 ${v.fuel_percent < 20 ? 'text-red-600 dark:text-red-400' : 'text-green-600 dark:text-green-400'}`}>
                  ⛽ {Math.round(v.fuel_percent)}%
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Period-based pickers. The input value is the *interval* (how many
// miles/hours/days from "now"), not the absolute due value. The page
// renders Current + Due readouts above the picker for context; this
// component owns only the period field and its preset shortcuts.

const MILE_PRESETS = [3_000, 5_000, 10_000, 15_000, 25_000, 50_000];

// Engine-hour presets are an order of magnitude smaller than mile
// presets: routine service intervals run ~250 / 500 / 1000 hours.
const HOUR_PRESETS = [250, 500, 1_000, 2_000, 5_000];

// Day presets covering the common service cadences: a quick weekly
// reminder all the way out to an annual DOT inspection.
const DAY_PRESETS = [7, 30, 60, 90, 180, 365];

function presetLabel(p: number): string {
  return p >= 1000 ? `+${p / 1000}k` : `+${p}`;
}

export function MilesPicker({
  value,
  onChange,
  placeholder = 'e.g. 3000',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <input
        type="number"
        min="0"
        step="1"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      <div className="flex flex-wrap gap-1 mt-1.5">
        {MILE_PRESETS.map(p => (
          <button
            key={p}
            type="button"
            title={`${p.toLocaleString()} miles from current`}
            onClick={() => onChange(String(p))}
            className="px-1.5 py-0.5 text-xs bg-muted border border-border rounded hover:bg-primary/20 hover:border-primary/50 transition-colors cursor-pointer"
          >
            {presetLabel(p)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function HoursPicker({
  value,
  onChange,
  placeholder = 'e.g. 500',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <input
        type="number"
        min="0"
        step="1"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      <div className="flex flex-wrap gap-1 mt-1.5">
        {HOUR_PRESETS.map(p => (
          <button
            key={p}
            type="button"
            title={`${p.toLocaleString()} hours from current`}
            onClick={() => onChange(String(p))}
            className="px-1.5 py-0.5 text-xs bg-muted border border-border rounded hover:bg-primary/20 hover:border-primary/50 transition-colors cursor-pointer"
          >
            {presetLabel(p)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function DaysPicker({
  value,
  onChange,
  placeholder = 'e.g. 30',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <input
        type="number"
        min="0"
        step="1"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
      />
      <div className="flex flex-wrap gap-1 mt-1.5">
        {DAY_PRESETS.map(p => (
          <button
            key={p}
            type="button"
            title={`${p} days from today`}
            onClick={() => onChange(String(p))}
            className="px-1.5 py-0.5 text-xs bg-muted border border-border rounded hover:bg-primary/20 hover:border-primary/50 transition-colors cursor-pointer"
          >
            {presetLabel(p)}
          </button>
        ))}
      </div>
    </div>
  );
}
