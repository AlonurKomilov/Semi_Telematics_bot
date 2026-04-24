import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import { ThemeToggle } from './ThemeToggle';
import { AvatarMenu } from './AvatarMenu';

export default function Layout() {
  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden bg-background">
        {/* Top bar */}
        <header className="h-14 bg-card border-b border-border flex items-center justify-between px-4 shrink-0">
          {/* Left: branding / breadcrumb placeholder */}
          <div />
          {/* Right: theme picker + user avatar */}
          <div className="flex items-center gap-1">
            <ThemeToggle />
            <AvatarMenu />
          </div>
        </header>
        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-4 lg:p-6 bg-background">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
