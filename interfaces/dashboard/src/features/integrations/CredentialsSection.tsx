/**
 * Shared "Credentials" section shell for every integration card.
 *
 * The credential *content* varies by auth_kind — Samsara shows a
 * per-company key matrix, single-token providers (Datatruck) show one
 * token + subdomain — but the SHELL (bordered card, KeyRound header,
 * optional right-aligned summary) is identical, so every provider's
 * credentials read as the same design family.  Matches the
 * <FeedsTable> "Synced data" header so the two sections sit
 * consistently in the card.
 */

import type { ReactNode } from 'react';
import { KeyRound } from 'lucide-react';

export default function CredentialsSection({
  title,
  headerRight,
  children,
}: {
  title: string;
  /** Right-aligned header content (health summary, add-company link). */
  headerRight?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mt-4 border border-border rounded-lg overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 bg-muted/40 border-b border-border">
        <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
          <KeyRound className="size-3.5" />
          {title}
        </span>
        {headerRight}
      </div>
      {children}
    </div>
  );
}
