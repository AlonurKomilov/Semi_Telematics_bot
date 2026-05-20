import { NavLink } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Truck } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useRoleView } from '../context/RoleViewContext';
import { PersonaSelector } from './PersonaSelector';
import { defaultNav, type NavGroup, type NavItem } from '../shells/nav/defaultNav';

// Sidebar is now presentational: it renders whatever ``navConfig`` is
// passed in.  Each shell (DefaultShell, FleetShell, DispatchShell,
// SafetyShell) supplies its own nav config from ``shells/nav/*Nav.ts``.
// Default to ``defaultNav`` for callers that don't specify one.
interface SidebarProps {
  navConfig?: NavGroup[];
}

export default function Sidebar({ navConfig = defaultNav }: SidebarProps) {
  const { user } = useAuth();
  const { t } = useTranslation();
  // ``viewHasAny`` is the active-persona-aware permission check from
  // RoleViewContext.  For non-switchable roles (Fleet/Safety/Dispatcher/
  // Driver) it falls back to the user's own permissions.  For Owner/
  // Admin previewing as another role, it uses that role's perm set so
  // the sidebar reflects what the previewed persona would see — no
  // bouncing back to "but they're Owner so show everything".
  const { viewHasAny } = useRoleView();

  const isFeatureVisible = (item: NavItem) => {
    if (item.path === '/payroll') return user?.payroll_enabled !== false;
    if (item.path === '/coaching') return user?.coaching_enabled !== false;
    return true;
  };

  const filterItems = (items: NavItem[]) =>
    items.filter((item) => {
      if (!isFeatureVisible(item)) return false;
      if (!item.permission) return true;
      const flags = Array.isArray(item.permission) ? item.permission : [item.permission];
      return viewHasAny(...flags);
    });

  return (
    <aside className="w-56 bg-card border-r border-border flex flex-col shrink-0 h-screen">
      {/* Logo + persona selector — sit on the same row so the persona
          control reads as part of "where am I" rather than "who am I".
          The selector lives next to the 4truck mark, right-aligned so
          there's a clear visual gap between brand and view-control. */}
      <div className="h-14 flex items-center px-3 border-b border-border shrink-0 gap-2">
        <Truck size={20} className="text-primary shrink-0" />
        <span className="text-lg font-bold text-foreground">4truck</span>
        <div className="ml-auto">
          <PersonaSelector />
        </div>
      </div>

      {/* Nav — scrolls independently */}
      <nav className="flex-1 overflow-y-auto py-2 scrollbar-thin">
        {navConfig.map((group, gi) => {
          const items = filterItems(group.items);
          if (items.length === 0) return null;
          return (
            <div key={group.titleKey ?? `_top-${gi}`}>
              {group.titleKey && (
                <div className="px-4 pt-4 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">
                  {t(group.titleKey)}
                </div>
              )}
              {items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.path}
                    to={item.path}
                    end={item.path === '/'}
                    className={({ isActive }) =>
                      `flex items-center gap-3 px-4 py-2 text-sm transition-colors ${
                        isActive
                          ? 'bg-primary/15 text-primary border-r-2 border-primary'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                      }`
                    }
                  >
                    <Icon size={16} className="shrink-0" />
                    {t(item.labelKey)}
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>
    </aside>
  );
}
