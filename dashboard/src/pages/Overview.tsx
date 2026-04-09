import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../api/client';
import { usePermissions } from '../hooks/usePermissions';
import type { DashboardStats } from '../types';

interface CardProps {
  label: string;
  value?: number;
  color: string;
  onClick?: () => void;
}

function Card({ label, value, color, onClick }: CardProps) {
  return (
    <button
      onClick={onClick}
      className={`bg-gray-900 border border-gray-800 rounded-xl p-5 text-left hover:border-gray-700 transition ${onClick ? 'cursor-pointer' : ''}`}
    >
      <p className="text-sm text-gray-400 mb-1">{label}</p>
      <p className={`text-3xl font-bold ${color}`}>{value ?? '—'}</p>
    </button>
  );
}

export default function Overview() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [error, setError] = useState('');
  const navigate = useNavigate();
  const { has } = usePermissions();

  useEffect(() => {
    apiJSON<DashboardStats>('/dashboard/stats')
      .then(setStats)
      .catch((e) => setError(e.message));
  }, []);

  if (error) return <p className="text-red-400">{error}</p>;
  if (!stats) return <p className="text-gray-500">Loading...</p>;

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

      {/* Alert cards */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {has('can_faults') && (
          <Card label="Vehicles w/ Faults" value={stats.faults} color="text-orange-400" />
        )}
        {has('can_fuel') && (
          <Card label="Low Fuel (< 20%)" value={stats.low_fuel} color="text-red-400" />
        )}
        {(has('can_alerts_all') || has('can_alerts_own')) && (
          <Card
            label="Pending Alerts"
            value={stats.pending_alerts}
            color="text-blue-400"
            onClick={() => navigate('/dispatch/alerts')}
          />
        )}
      </div>
    </div>
  );
}
