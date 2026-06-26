/**
 * Driver-specific Overview.
 *
 * Deliberately NOT a Pattern B composition — drivers have a single
 * dedicated view tuned to "your one truck" (status, fuel, faults,
 * location, my alerts / scorecard / routes / AI) and don't need
 * persona-tuned section ordering.  The non-driver Overview uses the
 * persona section model in ``./Overview.tsx``; this file is the
 * dedicated escape hatch for the Driver role.
 *
 * Lives in ``features/overview/`` so the Overview feature owns both
 * its persona-Pattern-B path and its driver-bespoke path side by
 * side; the top-level wrapper picks one or the other.
 */
import { useTranslation } from 'react-i18next';
import {
  Truck,
  Bell,
  MessageCircle,
  ShieldAlert,
  Route,
} from 'lucide-react';
import type { NavigateFunction } from 'react-router-dom';
import {
  PageHeader,
  KpiCard,
  EmptyState,
  Greeting,
} from '../../components/shell';
import { toneClasses, toneText } from '../../lib/status';
import type { DashboardStats } from '../../types';

interface DriverOverviewProps {
  stats: DashboardStats;
  navigate: NavigateFunction;
  greeting?: string;
}

export default function DriverOverview({
  stats,
  navigate,
  greeting,
}: DriverOverviewProps) {
  const { t } = useTranslation();
  const truck = stats.my_vehicle;
  const fuelTone =
    truck?.fuel_pct == null
      ? 'default'
      : truck.fuel_pct < 20
        ? 'critical'
        : truck.fuel_pct < 40
          ? 'warning'
          : 'positive';

  return (
    <div>
      {greeting !== undefined && (
        <Greeting
          name={greeting}
          context={
            stats.truck_num
              ? `Assigned vehicle: ${stats.truck_num}`
              : 'No vehicle assigned yet — ask your admin to link a truck.'
          }
        />
      )}
      <PageHeader
        icon={Truck}
        title={t('pages.overview_title')}
        description={t('pages.overview_desc')}
      />

      {!truck ? (
        <EmptyState
          icon={Truck}
          title="No vehicle data available"
          description="Ask your admin to assign a truck to your account so we can show you live status, fuel, and routes."
        />
      ) : (
        <>
          <div className="bg-card border border-border rounded-xl p-6 mb-6">
            <div className="flex items-center justify-between mb-4">
              <div>
                <p className="text-xs text-muted-foreground uppercase tracking-wide">
                  Assigned vehicle
                </p>
                <h2 className="text-xl font-semibold text-foreground mt-0.5">
                  {truck.name}
                </h2>
              </div>
              <span
                className={`px-3 py-1 rounded-full text-sm font-medium border ${
                  toneClasses(
                    truck.status === 'Moving' ? 'ok'
                    : truck.status === 'Idle' ? 'warn'
                    : 'danger',
                  )
                }`}
              >
                {truck.status}
              </span>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Speed</p>
                <p className="text-xl font-bold text-foreground mt-1">
                  {truck.speed_mph} mph
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Fuel level</p>
                <p
                  className={`text-xl font-bold mt-1 ${
                    fuelTone === 'critical'
                      ? toneText('danger')
                      : fuelTone === 'warning'
                        ? toneText('warn')
                        : fuelTone === 'positive'
                          ? toneText('ok')
                          : 'text-foreground'
                  }`}
                >
                  {truck.fuel_pct != null ? `${truck.fuel_pct}%` : '—'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Active faults</p>
                <p
                  className={`text-xl font-bold mt-1 ${
                    toneText(truck.faults > 0 ? 'warn' : 'ok')
                  }`}
                >
                  {truck.faults}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Company</p>
                <p className="text-xl font-bold text-foreground mt-1">
                  {truck.company || '—'}
                </p>
              </div>
            </div>
            {truck.location && (
              <p className="text-xs text-muted-foreground mt-3">{truck.location}</p>
            )}
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiCard
              label="My alerts"
              value={stats.my_alerts ?? 0}
              tone={(stats.my_alerts ?? 0) > 0 ? 'info' : 'default'}
              icon={Bell}
              hint="Open notifications for your truck"
              onClick={() => navigate('/alerts')}
            />
            <KpiCard
              label="My scorecard"
              value="View"
              tone="info"
              icon={ShieldAlert}
              hint="Safety score & ranking"
              onClick={() => navigate('/scorecards')}
            />
            <KpiCard
              label="My routes"
              value="View"
              tone="info"
              icon={Route}
              hint="Recent and upcoming trips"
              onClick={() => navigate('/routes')}
            />
            <KpiCard
              label="Ask AI"
              value="Chat"
              tone="info"
              icon={MessageCircle}
              hint="Faults, status, maintenance"
              onClick={() =>
                navigate('/ai/chat', {
                  state: {
                    initialMessage:
                      `How is my assigned truck doing? Give me a status summary.`,
                  },
                })
              }
            />
          </div>
        </>
      )}
    </div>
  );
}
