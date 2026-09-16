import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UserCog, LogOut, Palette } from '../lib/icons';
import { Avatar, AvatarFallback } from './ui/avatar';
import { useAuth } from '../context/AuthContext';
import { MODS_HREF, useCanMods } from '../mods';
import { ToursMenuItem } from '../features/tours/ToursMenuItem';
import { Dropdown } from './ui/context-menu';

export function AvatarMenu() {
  const { user, logout } = useAuth();
  const canMods = useCanMods();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  // Click-outside and Escape used to be two `document` listeners here.
  // They are the primitive's now — along with the thing they could not
  // give: this panel is PORTALLED, so it is no longer a descendant of
  // the topbar. Two consequences, one of them the reason for the
  // change. An ancestor with a `backdrop-filter` makes a descendant's
  // own filter a no-op, so while this lived in the header the header
  // could never be glass without this menu going see-through. And an
  // inline panel is at the mercy of every ancestor's `overflow` — the
  // persona flyout beside this one was erased by one, two levels up.

  const initials = (user?.display_name ?? 'U')
    .split(' ')
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();

  const role = user?.role?.replace(/_/g, ' ') ?? '';

  function go(path: string) {
    navigate(path);
    setOpen(false);
  }

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      className="w-56"
      trigger={(
        /* Trigger — avatar only.  The name and role used to live next
           to the circle, but they're already shown inside the opened
           menu so the top-bar version was just duplicating identity
           information.  Matches Samsara / Linear / GitHub's pattern of
           a compact identity dot that expands to a full panel.
           NO `onClick` and no `aria-expanded` of its own: the trigger
           owns both now, and a second toggle here would open the menu
           and close it again in the same click. */
        <button
          type="button"
          aria-label={user?.display_name || 'Account menu'}
          className="rounded-full hover:ring-2 hover:ring-border transition"
        >
          <Avatar className="h-8 w-8 shrink-0">
            <AvatarFallback className="text-xs bg-primary/20 text-foreground ring-1 ring-primary">{initials}</AvatarFallback>
          </Avatar>
        </button>
      )}
    >
      {/* User info */}
      <div className="px-4 py-3 border-b border-border">
        <p className="text-sm font-semibold text-foreground leading-tight">
          {user?.display_name || 'User'}
        </p>
        <p className="text-xs text-muted-foreground capitalize mt-0.5">{role}</p>
      </div>

      {/* AvatarMenu is the *personal* menu — only "things about
          me".  Account-wide settings (admins only) live in the
          sidebar under Admin → Settings, so they're discoverable
          alongside other admin pages instead of buried in the
          user dropdown. */}
      <div className="py-1">
        <MenuButton
          icon={<UserCog className="size-3.5" />}
          label="My Profile"
          onClick={() => go('/profile')}
        />
        {/* The second door to /mods. The topbar palette button is the
            first and the one people will use; this one exists because
            a page reachable only from inside a popover is a page most
            users never learn is a page. */}
        {canMods && (
          <MenuButton
            icon={<Palette className="size-3.5" />}
            label="Mods"
            onClick={() => go(MODS_HREF)}
          />
        )}
        {/* Tours are per person — progress and the next one belong
            in the personal menu, not on a topbar icon. */}
        <ToursMenuItem onGo={go} />
      </div>

      {/* Sign out */}
      <div className="border-t border-border py-1">
        <MenuButton
          icon={<LogOut className="size-3.5" />}
          label="Sign out"
          danger
          onClick={() => { logout(); setOpen(false); }}
        />
      </div>
    </Dropdown>
  );
}

// ── Internal helpers ─────────────────────────────────────────

export function MenuButton({
  icon,
  label,
  sub,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  /** A second, quieter line under the label — a state, never an action. */
  sub?: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        `w-full flex items-center gap-2.5 px-4 py-2 text-sm transition-colors text-left ` +
        (danger
          ? 'text-destructive hover:bg-destructive/10'
          : 'text-foreground hover:bg-muted/60')
      }
    >
      <span className={danger ? 'text-destructive' : 'text-muted-foreground'}>{icon}</span>
      <span className="min-w-0">
        <span className="block">{label}</span>
        {sub && <span className="block text-xs text-muted-foreground truncate">{sub}</span>}
      </span>
    </button>
  );
}
