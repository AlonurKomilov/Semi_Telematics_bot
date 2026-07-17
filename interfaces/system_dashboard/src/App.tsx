import { Routes, Route, NavLink, Link, useLocation, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { Building2, Receipt, ScrollText, LogOut, Activity, Users, AlertTriangle, MessageSquareWarning, DatabaseBackup, CalendarClock, ShieldCheck, Store, Cog, Gauge } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import Login from './pages/Login';
import Accounts from './pages/Accounts';
import AccountDetail from './pages/AccountDetail';
import AuditPage from './pages/Audit';
import InvoicesPage from './pages/Invoices';
import HealthPage from './pages/Health';
import UsersPage from './pages/Users';
import ErrorsPage from './pages/Errors';
import AIFeedbackPage from './pages/AIFeedback';
import RetentionPage from './pages/Retention';
import VendorDirectoryPage from './pages/VendorDirectory';
import PartsDirectoryPage from './pages/PartsDirectory';
import ScansPage from './pages/Scans';
import SchedulerPage from './pages/Scheduler';
import CapacityPage from './pages/Capacity';
import { clearToken, getToken } from './api/client';

export default function App() {
  const loc = useLocation();
  const nav = useNavigate();
  const [authed, setAuthed] = useState<boolean>(() => !!getToken());

  // Re-evaluate auth state when the route changes — login page sets
  // the token and pushes to / which re-renders this component.
  useEffect(() => {
    setAuthed(!!getToken());
  }, [loc.pathname]);

  // Unauthed visitor lands on /login regardless of path.  Soft gate —
  // the real authorization is server-side; this saves useless 401s.
  if (!authed && loc.pathname !== '/login') {
    return <Login />;
  }
  // The login page is full-screen (no chrome).
  if (loc.pathname === '/login') {
    return <Login />;
  }

  return (
    <div className="min-h-screen flex bg-slate-950">
      <Sidebar
        onLogout={() => {
          clearToken();
          setAuthed(false);
          nav('/login');
        }}
      />
      <main className="flex-1 min-w-0 px-6 py-6 overflow-x-auto">
        <div className="max-w-6xl mx-auto w-full">
          <Routes>
            <Route path="/" element={<Accounts />} />
            <Route path="/accounts" element={<Accounts />} />
            <Route path="/accounts/:id" element={<AccountDetail />} />
            <Route path="/invoices" element={<InvoicesPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/users" element={<UsersPage />} />
            <Route path="/errors" element={<ErrorsPage />} />
            <Route path="/ai-feedback" element={<AIFeedbackPage />} />
            <Route path="/retention" element={<RetentionPage />} />
            <Route path="/vendor-directory" element={<VendorDirectoryPage />} />
            <Route path="/parts-directory" element={<PartsDirectoryPage />} />
            <Route path="/scans" element={<ScansPage />} />
            <Route path="/scheduler" element={<SchedulerPage />} />
            <Route path="/capacity" element={<CapacityPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </div>
      </main>
    </div>
  );
}

// ── Sidebar ────────────────────────────────────────────────────

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  // ``end`` makes NavLink match the path exactly (so /accounts doesn't
  // stay highlighted when on /accounts/5 — actually we WANT it lit on
  // detail pages, so we leave end off for accounts).
  end?: boolean;
}

// Grouped so future admin sections (System, People) drop in without
// restructuring.  Each group renders its label + items.
const NAV_GROUPS: { title: string | null; items: NavItem[] }[] = [
  {
    title: 'Billing',
    items: [
      { to: '/accounts', label: 'Accounts', icon: Building2 },
      { to: '/invoices', label: 'Invoices', icon: Receipt },
      { to: '/audit',    label: 'Audit',    icon: ScrollText },
    ],
  },
  {
    title: 'Platform',
    items: [
      { to: '/health', label: 'Health', icon: Activity },
      { to: '/capacity', label: 'Capacity', icon: Gauge },
      { to: '/users',  label: 'Users',  icon: Users },
      { to: '/errors', label: 'Errors', icon: AlertTriangle },
      { to: '/ai-feedback', label: 'AI feedback', icon: MessageSquareWarning },
      { to: '/retention', label: 'Retention', icon: DatabaseBackup },
      { to: '/vendor-directory', label: 'Vendor directory', icon: Store },
      { to: '/parts-directory', label: 'Parts catalog', icon: Cog },
      { to: '/scans',     label: 'File scans', icon: ShieldCheck },
      { to: '/scheduler', label: 'Scheduler', icon: CalendarClock },
    ],
  },
];

function Sidebar({ onLogout }: { onLogout: () => void }) {
  return (
    <aside className="w-56 shrink-0 bg-slate-900 border-r border-slate-800 flex flex-col h-screen sticky top-0">
      <div className="px-4 py-4 border-b border-slate-800">
        <Link to="/accounts" className="text-sm font-semibold tracking-wider text-slate-200">
          4truck <span className="text-slate-500">/ system</span>
        </Link>
        <p className="text-[10px] text-slate-600 mt-1 uppercase tracking-wider">Operator console</p>
      </div>

      <nav className="flex-1 overflow-y-auto py-3">
        {NAV_GROUPS.map((group, gi) => (
          <div key={group.title ?? `g${gi}`} className="mb-2">
            {group.title && (
              <p className="px-4 py-1 text-[10px] uppercase tracking-wider text-slate-600">
                {group.title}
              </p>
            )}
            {group.items.map((item) => {
              const Icon = item.icon;
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                      isActive
                        ? 'bg-slate-800 text-white border-l-2 border-accent'
                        : 'text-slate-400 hover:text-slate-100 hover:bg-slate-800/50 border-l-2 border-transparent'
                    }`
                  }
                >
                  <Icon size={16} className="shrink-0" />
                  {item.label}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      <button
        onClick={onLogout}
        className="flex items-center gap-2 px-4 py-3 text-xs text-slate-400 hover:text-slate-200 border-t border-slate-800"
      >
        <LogOut size={14} /> Log out
      </button>
    </aside>
  );
}

function NotFound() {
  return (
    <div className="text-slate-400 text-sm py-12 text-center">
      Page not found.{' '}
      <Link to="/accounts" className="text-accent hover:underline">Back to accounts</Link>
    </div>
  );
}
