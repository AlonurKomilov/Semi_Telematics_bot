import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../api/client';
import { usePermissions } from '../hooks/usePermissions';
import { useAuth } from '../context/AuthContext';
import type { DashboardStats } from '../types';

interface CardProps {
  label: string;
  value?: number | string;
  color: string;
  onClick?: () => void;
  subtitle?: string;
}

function Card({ label, value, color, onClick, subtitle }: CardProps) {
  return (
    <button
      onClick={onClick}
      className={`bg-gray-900 border border-gray-800 rounded-xl p-5 text-left hover:border-gray-700 transition ${onClick ? 'cursor-pointer' : ''}`}
    >
      <p className="text-sm text-gray-400 mb-1">{label}</p>
      <p className={`text-3xl font-bold ${color}`}>{value ?? '—'}</p>
      {subtitle && <p className="text-xs text-gray-500 mt-1">{subtitle}</p>}
    </button>
  );
}

// ── Driver Overview ──────────────────────────────────────────

function DriverOverview({ stats, navigate }: { stats: DashboardStats; navigate: ReturnType<typeof useNavigate> }) {
  const truck = stats.my_truck;
  const statusColor = truck?.status === 'Moving' ? 'text-green-400' : truck?.status === 'Idle' ? 'text-yellow-400' : 'text-red-400';

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">🚛 My Dashboard</h1>
      <p className="text-sm text-gray-400 mb-6">
        {stats.truck_num ? `Assigned vehicle: ${stats.truck_num}` : 'No vehicle assigned — contact your admin'}
      </p>

      {truck && (
        <>
          {/* My truck status */}
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-white">{truck.name}</h2>
              <span className={`px-3 py-1 rounded-full text-sm font-medium ${
                truck.status === 'Moving' ? 'bg-green-900/30 text-green-400' :
                truck.status === 'Idle' ? 'bg-yellow-900/30 text-yellow-400' :
                'bg-red-900/30 text-red-400'
              }`}>
                {truck.status}
              </span>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-gray-500">Speed</p>
                <p className={`text-xl font-bold ${statusColor}`}>{truck.speed_mph} mph</p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Fuel Level</p>
                <p className={`text-xl font-bold ${
                  truck.fuel_pct !== null && truck.fuel_pct < 20 ? 'text-red-400' :
                  truck.fuel_pct !== null && truck.fuel_pct < 40 ? 'text-yellow-400' : 'text-green-400'
                }`}>
                  {truck.fuel_pct !== null ? `${truck.fuel_pct}%` : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Active Faults</p>
                <p className={`text-xl font-bold ${truck.faults > 0 ? 'text-orange-400' : 'text-green-400'}`}>
                  {truck.faults}
                </p>
              </div>
              <div>
                <p className="text-xs text-gray-500">Company</p>
                <p className="text-xl font-bold text-white">{truck.company || '—'}</p>
              </div>
            </div>
            {truck.location && (
              <p className="text-xs text-gray-500 mt-3">📍 {truck.location}</p>
            )}
          </div>

          {/* Quick action cards */}
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            <Card
              label="My Alerts"
              value={stats.my_alerts ?? 0}
              color={(stats.my_alerts ?? 0) > 0 ? 'text-blue-400' : 'text-gray-400'}
              onClick={() => navigate('/dispatch/alerts')}
            />
            <Card
              label="My Scorecard"
              value="View"
              color="text-blue-400"
              onClick={() => navigate('/safety/scorecards')}
            />
            <Card
              label="My Routes"
              value="View"
              color="text-blue-400"
              onClick={() => navigate('/dispatch/routes')}
            />
          </div>
        </>
      )}

      {!truck && (
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
          <p className="text-gray-500 text-lg">No vehicle data available</p>
          <p className="text-gray-600 text-sm mt-2">
            Ask your admin to assign a truck number to your account.
          </p>
        </div>
      )}
    </div>
  );
}

// ── Fleet/Admin Overview ─────────────────────────────────────

function FleetOverview({ stats, navigate, has }: {
  stats: DashboardStats;
  navigate: ReturnType<typeof useNavigate>;
  has: (flag: string) => boolean;
}) {
  const f = stats.fleet || {};

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Overview</h1>

      {/* Fleet status cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <Card
          label="Total Vehicles"
          value={f.total}
          color="text-white"
          onClick={has('can_truck_all') ? () => navigate('/fleet/vehicles') : undefined}
        />
        <Card label="Moving" value={f.moving} color="text-green-400" />
        <Card label="Idle" value={f.idle} color="text-yellow-400" />
        <Card label="Stopped" value={f.stopped} color="text-red-400" />
      </div>

      {/* Detail cards — shown based on role permissions */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {has('can_faults') && stats.faults !== undefined && (
          <Card
            label="Vehicles w/ Faults"
            value={stats.faults}
            color="text-orange-400"
            onClick={() => navigate('/fleet/vehicles')}
          />
        )}
        {has('can_fuel') && stats.low_fuel !== undefined && (
          <Card label="Low Fuel (< 20%)" value={stats.low_fuel} color="text-red-400" />
        )}
        {(has('can_alerts_all') || has('can_alerts_own')) && stats.pending_alerts !== undefined && (
          <Card
            label="Pending Alerts"
            value={stats.pending_alerts}
            color="text-blue-400"
            onClick={() => navigate('/dispatch/alerts')}
          />
        )}
        {(has('can_alerts_all') || has('can_alerts_own')) && ((stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0) > 0) && (
          <Card
            label="Unsafe Parking"
            value={(stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0)}
            color="text-red-400"
            onClick={() => navigate('/safety/parking')}
          />
        )}
        {has('can_maintenance_all') && stats.maintenance_due !== undefined && stats.maintenance_due > 0 && (
          <Card
            label="Maintenance Due"
            value={stats.maintenance_due}
            color="text-yellow-400"
            onClick={() => navigate('/maintenance')}
          />
        )}
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────

export default function Overview() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { has } = usePermissions();
  const { user } = useAuth();
  const isDriver = user?.role === 'driver';

  useEffect(() => {
    apiJSON<DashboardStats>('/dashboard/stats')
      .then(setStats)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!stats) return <p className="text-gray-500">Loading...</p>;

  if (isDriver) {
    return <DriverOverview stats={stats} navigate={navigate} />;
  }

  return <FleetOverview stats={stats} navigate={navigate} has={has} />;
}
