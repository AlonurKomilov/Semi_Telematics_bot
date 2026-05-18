import { useState, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { UserCog, LogOut, ChevronDown } from 'lucide-react';
import { Avatar, AvatarFallback } from './ui/avatar';
import { useAuth } from '../context/AuthContext';

export function AvatarMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

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
    <div className="relative" ref={ref}>
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-muted/50 transition-colors"
      >
        <Avatar className="h-7 w-7 shrink-0">
          <AvatarFallback className="text-xs bg-primary/20 text-primary">{initials}</AvatarFallback>
        </Avatar>
        <div className="hidden sm:flex flex-col items-start min-w-0 max-w-[130px]">
          <span className="text-sm font-medium text-foreground leading-tight truncate w-full">
            {user?.display_name || 'User'}
          </span>
          <span className="text-[11px] text-muted-foreground capitalize leading-tight">{role}</span>
        </div>
        <ChevronDown size={13} className="text-muted-foreground shrink-0" />
      </button>

      {/* Dropdown panel */}
      {open && (
        <div className="absolute right-0 top-11 z-50 w-56 bg-popover border border-border rounded-xl shadow-xl overflow-hidden">
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
              icon={<UserCog size={14} />}
              label="My Profile"
              onClick={() => go('/profile')}
            />
          </div>

          {/* Sign out */}
          <div className="border-t border-border py-1">
            <MenuButton
              icon={<LogOut size={14} />}
              label="Sign out"
              danger
              onClick={() => { logout(); setOpen(false); }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ── Internal helpers ─────────────────────────────────────────

function MenuButton({
  icon,
  label,
  danger,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        `w-full flex items-center gap-2.5 px-4 py-2 text-sm transition-colors ` +
        (danger
          ? 'text-destructive hover:bg-destructive/10'
          : 'text-foreground hover:bg-muted/60')
      }
    >
      <span className={danger ? 'text-destructive' : 'text-muted-foreground'}>{icon}</span>
      {label}
    </button>
  );
}
