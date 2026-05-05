import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts';
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
      className={`bg-card border border-border rounded-xl p-5 text-left hover:border-border transition ${onClick ? 'cursor-pointer' : ''}`}
    >
      <p className="text-sm text-muted-foreground mb-1">{label}</p>
      <p className={`text-3xl font-bold ${color}`}>{value ?? '—'}</p>
      {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
    </button>
  );
}

// ── Driver Overview ──────────────────────────────────────────

function DriverOverview({ stats, navigate }: { stats: DashboardStats; navigate: ReturnType<typeof useNavigate> }) {
  const truck = stats.my_vehicle;
  const statusColor = truck?.status === 'Moving' ? 'text-green-600 dark:text-green-400' : truck?.status === 'Idle' ? 'text-yellow-600 dark:text-yellow-400' : 'text-destructive';

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">🚛 My Dashboard</h1>
      <p className="text-sm text-muted-foreground mb-6">
        {stats.truck_num ? `Assigned vehicle: ${stats.truck_num}` : 'No vehicle assigned — contact your admin'}
      </p>

      {truck && (
        <>
          {/* My truck status */}
          <div className="bg-card border border-border rounded-xl p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-semibold text-foreground">{truck.name}</h2>
              <span className={`px-3 py-1 rounded-full text-sm font-medium ${
                truck.status === 'Moving' ? 'bg-green-500/15 text-green-700 dark:text-green-400' :
                truck.status === 'Idle' ? 'bg-yellow-500/15 text-yellow-700 dark:text-yellow-400' :
                'bg-red-500/15 text-red-600 dark:text-red-400'
              }`}>
                {truck.status}
              </span>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Speed</p>
                <p className={`text-xl font-bold ${statusColor}`}>{truck.speed_mph} mph</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Fuel Level</p>
                <p className={`text-xl font-bold ${
                  truck.fuel_pct !== null && truck.fuel_pct < 20 ? 'text-destructive' :
                  truck.fuel_pct !== null && truck.fuel_pct < 40 ? 'text-yellow-700 dark:text-yellow-400' : 'text-green-600 dark:text-green-400'
                }`}>
                  {truck.fuel_pct !== null ? `${truck.fuel_pct}%` : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Active Faults</p>
                <p className={`text-xl font-bold ${truck.faults > 0 ? 'text-orange-600 dark:text-orange-400' : 'text-green-600 dark:text-green-400'}`}>
                  {truck.faults}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Company</p>
                <p className="text-xl font-bold text-foreground">{truck.company || '—'}</p>
              </div>
            </div>
            {truck.location && (
              <p className="text-xs text-muted-foreground mt-3">📍 {truck.location}</p>
            )}
          </div>

          {/* Quick action cards */}
          <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
            <Card
              label="My Alerts"
              value={stats.my_alerts ?? 0}
              color={(stats.my_alerts ?? 0) > 0 ? 'text-primary' : 'text-muted-foreground'}
              onClick={() => navigate('/dispatch/alerts')}
            />
            <Card
              label="My Scorecard"
              value="View"
              color="text-primary"
              onClick={() => navigate('/safety/scorecards')}
            />
            <Card
              label="My Routes"
              value="View"
              color="text-primary"
              onClick={() => navigate('/dispatch/routes')}
            />
            <Card
              label="Ask AI about my truck"
              value="Chat"
              color="text-primary"
              subtitle="Faults, status, maintenance"
              onClick={() => navigate('/ai/chat', { state: { initialMessage: `How is my assigned truck doing? Give me a status summary.` } })}
            />
          </div>
        </>
      )}

      {!truck && (
        <div className="bg-card border border-border rounded-xl p-8 text-center">
          <p className="text-muted-foreground text-lg">No vehicle data available</p>
          <p className="text-muted-foreground text-sm mt-2">
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
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <Card
          label="Total Vehicles"
          value={f.total}
          color="text-foreground"
          onClick={has('can_vehicle_all') ? () => navigate('/fleet/vehicles') : undefined}
        />
        <Card label="Moving" value={f.moving} color="text-green-600 dark:text-green-400" />
        <Card label="Idle" value={f.idle} color="text-yellow-700 dark:text-yellow-400" />
        <Card label="Stopped" value={f.stopped} color="text-destructive" />
      </div>

      {/* Fleet status donut chart */}
      {(f.moving || f.idle || f.stopped) && (
        <div className="bg-card border border-border rounded-xl p-5 mb-8">
          <p className="text-sm text-muted-foreground mb-3 font-medium">Fleet Status Distribution</p>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie
                data={[
                  { name: 'Moving',  value: f.moving  || 0 },
                  { name: 'Idle',    value: f.idle    || 0 },
                  { name: 'Stopped', value: f.stopped || 0 },
                ]}
                cx="50%"
                cy="50%"
                innerRadius={55}
                outerRadius={85}
                paddingAngle={3}
                dataKey="value"
              >
                <Cell fill="#22c55e" />
                <Cell fill="#eab308" />
                <Cell fill="#ef4444" />
              </Pie>
              <Tooltip
                contentStyle={{ background: '#111827', border: '1px solid #374151', borderRadius: 8, color: '#f9fafb' }}
              />
              <Legend iconType="circle" iconSize={10} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Detail cards — shown based on role permissions */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {has('can_faults') && stats.faults !== undefined && (
          <Card
            label="Vehicles w/ Faults"
            value={stats.faults}
            color="text-orange-600 dark:text-orange-400"
            onClick={() => navigate('/fleet/vehicles')}
          />
        )}
        {has('can_faults') && (
          <Card
            label="AI Fleet Briefing"
            value="Generate"
            color="text-primary"
            subtitle="Status summary & recommendations"
            onClick={() => navigate('/ai/chat?tab=briefing')}
          />
        )}
        {has('can_fuel') && stats.low_fuel !== undefined && (
          <Card label="Low Fuel (< 20%)" value={stats.low_fuel} color="text-destructive" />
        )}
        {(has('can_alerts_all') || has('can_alerts_own')) && stats.pending_alerts !== undefined && (
          <Card
            label="Pending Alerts"
            value={stats.pending_alerts}
            color="text-primary"
            onClick={() => navigate('/dispatch/alerts')}
          />
        )}
        {(has('can_alerts_all') || has('can_alerts_own')) && ((stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0) > 0) && (
          <Card
            label="Unsafe Parking"
            value={(stats.unsafe_parking ?? 0) + (stats.unknown_parking ?? 0)}
            color="text-destructive"
            onClick={() => navigate('/safety/parking')}
          />
        )}
        {has('can_maintenance_all') && stats.maintenance_due !== undefined && stats.maintenance_due > 0 && (
          <Card
            label="Maintenance Due"
            value={stats.maintenance_due}
            color="text-yellow-700 dark:text-yellow-400"
            onClick={() => navigate('/maintenance')}
          />
        )}
      </div>
    </div>
  );
}

// ── Main component ───────────────────────────────────────────

export default function Overview() {
  const navigate = useNavigate();
  const { has } = usePermissions();
  const { user } = useAuth();
  const isDriver = user?.role === 'driver';

  // React Query (Phase E23): cache /dashboard/stats with a short stale
  // window so navigating back to Overview is instant; refetch is
  // automatic when the cached value passes ``staleTime``.
  const { data: stats, error: queryError } = useQuery<DashboardStats>({
    queryKey: ['dashboard-stats'],
    queryFn: () => apiJSON<DashboardStats>('/dashboard/stats'),
  });
  const error = queryError instanceof Error ? queryError.message : (queryError ? 'Failed to load' : '');

  if (error) return <p className="text-destructive">{error}</p>;
  if (!stats) return <p className="text-muted-foreground">Loading...</p>;

  if (isDriver) {
    return <DriverOverview stats={stats} navigate={navigate} />;
  }

  return <FleetOverview stats={stats} navigate={navigate} has={has} />;
}
