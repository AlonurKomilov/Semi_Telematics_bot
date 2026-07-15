import { useNavigate } from 'react-router-dom';
import { Truck } from 'lucide-react';
import { toneClasses, type Tone } from '../../../lib/status';
import { Tip } from '../../../components/tooltip';

/**
 * Renders the vehicles referenced by an AI answer as clickable, severity-
 * coloured chips that deep-link to each vehicle's page.  The data comes from
 * the `tool_results` already delivered on the chat `done` event — every tool
 * returns its rows under a known list key with a `"vehicle"` field (the human
 * truck name), so we can extract them generically.  Additive: when an answer
 * referenced no vehicles (e.g. a knowledge question) nothing renders.
 */

export interface VehicleRef {
  vehicle: string;
  company?: string;
  tone: Tone;
  note?: string;
}

// Tool list-keys whose items carry a per-vehicle row.  Each maps to how that
// row's severity tone + short note are derived.
const TONE_RANK: Record<Tone, number> = { danger: 4, warn: 3, info: 2, ok: 1, neutral: 0 };

function round1(n: unknown): number | null {
  const v = typeof n === 'number' ? n : Number(n);
  return Number.isFinite(v) ? Math.round(v * 10) / 10 : null;
}

/** Derive {tone, note} for one row, keyed by which list it came from. */
function classifyRow(listKey: string, row: Record<string, unknown>): { tone: Tone; note?: string } {
  switch (listKey) {
    case 'alerts': {
      const sev = String(row.severity ?? '').toLowerCase();
      const tone: Tone = sev === 'critical' ? 'danger' : sev === 'warning' || sev === 'warn' ? 'warn' : 'info';
      const type = row.type ? String(row.type).replace(/_/g, ' ') : 'alert';
      return { tone, note: type };
    }
    case 'overdue_tasks':
      return { tone: 'danger', note: row.type ? `overdue ${String(row.type).replace(/_/g, ' ')}` : 'overdue' };
    case 'due_soon_tasks':
      return { tone: 'warn', note: row.type ? `${String(row.type).replace(/_/g, ' ')} due soon` : 'due soon' };
    case 'pending_tasks':
      return { tone: 'info', note: row.type ? `due ${String(row.type).replace(/_/g, ' ')}` : 'pending' };
    case 'off_vehicles':
      return { tone: 'neutral', note: 'off' };
    case 'idling_vehicles':
      return { tone: 'warn', note: 'idling' };
    case 'rolling_vehicles':
      return { tone: 'ok', note: 'moving' };
    case 'vehicles':
    default: {
      // The generic "vehicles" list is shared by several tools — distinguish
      // by the distinctive field each one adds.
      const days = round1(row.days_stopped);
      if (days != null) {
        const tone: Tone = days >= 7 ? 'danger' : days >= 3 ? 'warn' : 'info';
        return { tone, note: `${days}d stopped` };
      }
      const parked = round1(row.duration_days);
      if (parked != null) {
        const tone: Tone = parked >= 5 ? 'danger' : parked >= 2 ? 'warn' : 'info';
        return { tone, note: `parked ${parked}d` };
      }
      const fuel = round1(row.fuel_pct);
      if (fuel != null) {
        const tone: Tone = fuel < 15 ? 'danger' : fuel < 30 ? 'warn' : 'info';
        return { tone, note: `${fuel}% fuel` };
      }
      return { tone: 'neutral' };
    }
  }
}

/**
 * Pull a deduplicated, severity-ranked list of referenced vehicles out of the
 * chat's `tool_results` payload.  Pure — exported for unit testing.
 */
export function extractVehicleRefs(toolResults: unknown): VehicleRef[] {
  if (!Array.isArray(toolResults)) return [];
  const LIST_KEYS = ['vehicles', 'alerts', 'off_vehicles', 'idling_vehicles', 'rolling_vehicles', 'overdue_tasks', 'due_soon_tasks', 'pending_tasks'];
  const byVehicle = new Map<string, VehicleRef>();

  for (const tr of toolResults) {
    const data = (tr as { data?: unknown })?.data;
    if (!data || typeof data !== 'object') continue;
    for (const key of LIST_KEYS) {
      const list = (data as Record<string, unknown>)[key];
      if (!Array.isArray(list)) continue;
      for (const raw of list) {
        if (!raw || typeof raw !== 'object') continue;
        const row = raw as Record<string, unknown>;
        const vehicle = typeof row.vehicle === 'string' ? row.vehicle.trim() : '';
        if (!vehicle) continue;
        const { tone, note } = classifyRow(key, row);
        const company = typeof row.company === 'string' && row.company ? row.company : undefined;
        const ref: VehicleRef = { vehicle, company, tone, note };
        const prev = byVehicle.get(vehicle);
        // Keep the highest-severity sighting; carry a company code forward if a
        // later, lower-severity row is the one that has it.
        if (!prev || TONE_RANK[tone] > TONE_RANK[prev.tone]) {
          byVehicle.set(vehicle, { ...ref, company: company ?? prev?.company });
        } else if (!prev.company && company) {
          byVehicle.set(vehicle, { ...prev, company });
        }
      }
    }
  }
  return [...byVehicle.values()];
}

export function ReferencedVehicles({ toolResults }: { toolResults: unknown }) {
  const navigate = useNavigate();
  const refs = extractVehicleRefs(toolResults);
  if (refs.length === 0) return null;

  function open(ref: VehicleRef) {
    const qs = ref.company ? `?company=${encodeURIComponent(ref.company)}` : '';
    navigate(`/vehicles/${encodeURIComponent(ref.vehicle)}${qs}`);
  }

  return (
    <div className="mt-2 pt-2 border-t border-border/40">
      <div className="text-3xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
        Vehicles in this answer
      </div>
      <div className="flex flex-wrap gap-1.5">
        {refs.map((r) => (
          <Tip key={r.vehicle} label={r.note ? `${r.vehicle} — ${r.note}` : `Open ${r.vehicle}`}>
            <button
              onClick={() => open(r)}
              className={`inline-flex items-center gap-1 px-2 py-1 rounded-md border text-2xs font-medium cursor-pointer transition-opacity hover:opacity-80 ${toneClasses(r.tone)}`}
            >
              <Truck size={12} aria-hidden />
              <span>{r.vehicle}</span>
              {r.note && <span className="opacity-70">· {r.note}</span>}
            </button>
          </Tip>
        ))}
      </div>
    </div>
  );
}
