import { useLocation, Link } from 'react-router-dom';
import { ChevronRight, Home } from 'lucide-react';
import { findRouteByPath } from './routeRegistry';

export default function Breadcrumb() {
  const { pathname } = useLocation();
  const route = findRouteByPath(pathname);

  if (!route) {
    return null;
  }

  const isHome = route.path === '/';

  return (
    <nav className="flex items-center gap-1.5 text-sm text-muted-foreground min-w-0">
      <Link
        to="/"
        className="inline-flex items-center gap-1 hover:text-foreground transition shrink-0"
      >
        <Home size={14} />
        <span className="hidden sm:inline">Home</span>
      </Link>
      {!isHome && (
        <>
          <ChevronRight size={14} className="shrink-0" />
          <span className="text-muted-foreground/80 hidden sm:inline">{route.group}</span>
          <ChevronRight size={14} className="shrink-0 hidden sm:inline" />
          <span className="text-foreground font-medium truncate">{route.label}</span>
        </>
      )}
    </nav>
  );
}
