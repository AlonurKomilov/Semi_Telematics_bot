import { useEffect, useState } from 'react';
import { Cell, List, Placeholder, Section, Spinner } from '@telegram-apps/telegram-ui';
import { apiJSON } from '../api/client';
import type { Vehicle } from '../types';

interface Props {
  onGoToMap: () => void;
}

function vehicleStatus(v: Vehicle): 'moving' | 'idle' | 'stopped' {
  if (v.engine_state === 'On') return (v.speed_mph ?? 0) > 2 ? 'moving' : 'idle';
  return 'stopped';
}

export function TrucksPage({ onGoToMap }: Props) {
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [search, setSearch] = useState('');

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(false);
    try {
      const data = await apiJSON<{ vehicles: Vehicle[] }>('/api/fleet/vehicles');
      setVehicles(data.vehicles ?? []);
    } catch (e) {
      console.error('Failed to load vehicles:', e);
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  const q = search.trim().toLowerCase();
  const filtered = q
    ? vehicles.filter(v =>
        v.name.toLowerCase().includes(q) ||
        (v.company ?? '').toLowerCase().includes(q) ||
        (v.address ?? '').toLowerCase().includes(q),
      )
    : vehicles;

  // ── States ────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="centered">
        <Spinner size="l" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="centered">
        <Placeholder header="Failed to Load" description="Could not fetch fleet vehicles. Please try again.">
          ⚠️
        </Placeholder>
      </div>
    );
  }

  if (vehicles.length === 0) {
    return (
      <div className="centered">
        <Placeholder header="No Vehicles" description="No active vehicles found for your account.">
          🚛
        </Placeholder>
      </div>
    );
  }

  // ── Render ────────────────────────────────────────────────────────

  return (
    <div style={{ paddingBottom: 8 }}>
      {/* Search bar */}
      <input
        className="search-input"
        type="search"
        placeholder="Search vehicles…"
        value={search}
        onChange={e => setSearch(e.target.value)}
      />

      {filtered.length === 0 ? (
        <div className="centered">
          <Placeholder header="No Results" description={`No vehicles matching "${search}"`}>
            🔍
          </Placeholder>
        </div>
      ) : (
        <List>
          <Section header={`${filtered.length} vehicle${filtered.length !== 1 ? 's' : ''}`}>
            {filtered.map(v => {
              const status = vehicleStatus(v);
              const speed = v.speed_mph != null ? `${Math.round(v.speed_mph)} mph` : null;
              const fuel = v.fuel_percent != null ? `${v.fuel_percent}%` : null;

              const details = [
                speed && `⚡ ${speed}`,
                fuel && `⛽ ${fuel}`,
                v.fault_count ? `⚠️ ${v.fault_count} fault${v.fault_count !== 1 ? 's' : ''}` : null,
              ].filter(Boolean).join('  ·  ');

              return (
                <Cell
                  key={v.id ?? v.name}
                  subtitle={details || v.address?.slice(0, 60) || undefined}
                  after={
                    <span className={`status-badge ${status}`}>{status}</span>
                  }
                  onClick={() => {
                    onGoToMap();
                  }}
                >
                  {v.name}
                </Cell>
              );
            })}
          </Section>
        </List>
      )}
    </div>
  );
}
