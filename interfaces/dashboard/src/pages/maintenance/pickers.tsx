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

  // Dedupe by (company, name) — defensive against the fleet API
  // occasionally returning duplicate rows (cache merge / pagination
  // overlap).  Without this the picker shows "103 OSY" three times in
  // a row, which is confusing and breaks the React key.
  const deduped = (() => {
    const seen = new Set<string>();
    const out: FleetVehicle[] = [];
    for (const v of vehicles) {
      const key = `${(v.company || '').toLowerCase()}::${(v.name || '').toLowerCase()}`;
      if (!seen.has(key)) {
        seen.add(key);
        out.push(v);
      }
    }
    return out;
  })();

  const filtered = query.trim()
    ? deduped.filter(
        (v) =>
          v.name.toLowerCase().includes(query.toLowerCase()) ||
          v.company.toLowerCase().includes(query.toLowerCase()),
      )
    : deduped;

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
              // Composite key — vehicle names can repeat across
              // companies (one "103" per company is legit), so a
              // name-only key collides and React warns / reorders
              // wrong.  ``${company}::${name}`` is unique per fleet
              // row and survives the de-dup pass above.
              key={`${v.company}::${v.name}`}
              onMouseDown={() => select(v)}
              className="flex items-center gap-2 px-3 py-2 hover:bg-muted cursor-pointer"
            >
              <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_DOT[v.status] || 'bg-muted-foreground/40'}`} />
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

// Period-based pickers.  By default the input value is the *interval*
// (how many miles/hours/days from "now") and the page renders a
// "Current / Due" readout above the picker; the form-submit code
// computes ``due = current + interval`` before sending to the API.
//
// When telemetry is missing (``mode='absolute'``), the interval model
// breaks because ``current`` is unknown.  The picker then renders the
// presets as ABSOLUTE targets instead and shows a one-line hint
// explaining the change in semantics.  This is the difference between
// "schedule oil at +5,000 miles" and "schedule oil at 250,000 miles" —
// the user input is the same number, but the meaning differs.

const MILE_PRESETS = [3_000, 5_000, 10_000, 15_000, 25_000, 50_000];

// Engine-hour presets are an order of magnitude smaller than mile
// presets: routine service intervals run ~250 / 500 / 1000 hours.
const HOUR_PRESETS = [250, 500, 1_000, 2_000, 5_000];

// Day presets covering the common service cadences: a quick weekly
// reminder all the way out to an annual DOT inspection.
const DAY_PRESETS = [7, 30, 60, 90, 180, 365];

type PickerMode = 'period' | 'absolute';

function presetLabel(p: number): string {
  // Plain "3k" / "30" — no "+" prefix, which used to imply "add to
  // existing value".  The buttons SET the input, not ADD, so the
  // prefix was misleading.  The hover title (set by each picker) still
  // disambiguates period vs absolute mode.
  return p >= 1000 ? `${p / 1000}k` : `${p}`;
}

export function MilesPicker({
  value,
  onChange,
  placeholder = 'e.g. 3000',
  mode = 'period',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mode?: PickerMode;
}) {
  const presetTitle = (p: number) =>
    mode === 'absolute'
      ? `${p.toLocaleString()} miles (absolute target — no telemetry to add from)`
      : `${p.toLocaleString()} miles from current odometer`;
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
      {mode === 'absolute' && (
        <p className="text-[11px] text-muted-foreground mt-1">
          No odometer telemetry — value is the absolute target.
        </p>
      )}
      <div className="flex flex-wrap gap-1 mt-1.5">
        {MILE_PRESETS.map(p => (
          <button
            key={p}
            type="button"
            title={presetTitle(p)}
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
  mode = 'period',
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mode?: PickerMode;
}) {
  const presetTitle = (p: number) =>
    mode === 'absolute'
      ? `${p.toLocaleString()} hours (absolute target — no telemetry to add from)`
      : `${p.toLocaleString()} hours from current engine hours`;
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
      {mode === 'absolute' && (
        <p className="text-[11px] text-muted-foreground mt-1">
          No engine-hours telemetry — value is the absolute target.
        </p>
      )}
      <div className="flex flex-wrap gap-1 mt-1.5">
        {HOUR_PRESETS.map(p => (
          <button
            key={p}
            type="button"
            title={presetTitle(p)}
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
  // Days are always a period from today — no absolute-mode variant
  // because "today" is always known (unlike odometer/engine hours).
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
