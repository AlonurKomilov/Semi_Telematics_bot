/**
 * Group delivery — the admin surface for WHERE account alerts go (group /
 * forum routing), under the Alerts tab beside the personal Notification
 * preferences.  Re-homed from Settings (2026-07-22) so a role MANAGER
 * configures their OWN role's group here — binding the group, attaching a
 * Sub bot, tuning topics — instead of asking an owner to do it in Settings.
 *
 * The bot CREDENTIAL (connect/disconnect the account bot + token) AND the
 * Single ↔ Sub bots mode selector stay on Settings → Telegram Bot (both
 * owner topology decisions); this tab is the operational routing that
 * FOLLOWS the mode: group binding, Sub bots, per-type switches, kinds,
 * custom topics.
 *
 * Access: can_manage_account (owner/admin — full, incl. the mode selector)
 * OR can_manage_role_bot (a role manager — sees only their own row, which
 * the API's `manageable` list enforces server-side).  Everyone else is
 * redirected back to the board.
 */
import { useQuery } from '@tanstack/react-query';
import { Link, Navigate } from 'react-router-dom';
import { Send } from 'lucide-react';
import { apiJSON } from '@/api/client';
import type { BotConfig } from '@/types';
import { PageHeader, CardSkeleton, EmptyState } from '@/components/shell';
import { useViewPermissions } from '@/hooks/useViewPermissions';
import { AlertsTabs } from './AlertsTabs';
import AlertRoutingSection from './delivery/AlertRoutingSection';
import ForumRoutingSection from './delivery/ForumRoutingSection';

export default function GroupDelivery() {
  const { has, ready } = useViewPermissions();
  const canManageAccount = has('can_manage_account');
  const canManage = canManageAccount || has('can_manage_role_bot');

  const { data: botConfig, isLoading } = useQuery({
    queryKey: ['admin-bot-config'],   // shared cache with Settings
    queryFn: () => apiJSON<BotConfig>('/admin/bot-config'),
    enabled: canManage,
  });

  // Direct navigation by someone with neither grant → back to the board.
  // Only once the answer is authoritative: on a preview view permissions
  // arrive from a second request, and bouncing during that window broke
  // every deep link here (the ``replace`` also discarded the URL).
  if (ready && !canManage) return <Navigate to="/alerts" replace />;

  const header = (
    <>
      <AlertsTabs />
      <PageHeader
        icon={Send}
        title="Group delivery"
        description="Route this account's alerts to Telegram groups and topics. Personal DMs are separate — each person sets those under Notification preferences."
      />
    </>
  );

  // Permissions still in flight: the bot-config query is gated on
  // ``canManage``, so a disabled query would fall through to the
  // "no bot connected" empty state and blame the account for it.
  if (!ready) {
    return <div>{header}<CardSkeleton message="Checking access…" /></div>;
  }

  if (isLoading) {
    return <div>{header}<CardSkeleton message="Loading group delivery…" /></div>;
  }

  // Routing needs a working bot; only an owner can connect one (Settings).
  if (!botConfig?.has_bot) {
    return (
      <div>
        {header}
        <EmptyState
          icon={Send}
          title="No Telegram bot connected"
          description="Group routing lives here once the account's Telegram bot is connected. An owner connects it on Settings → Telegram Bot."
          action={canManageAccount ? (
            <Link
              to="/settings"
              className="px-3 py-1.5 border border-primary/40 bg-primary/15 hover:bg-primary/25 text-primary rounded text-xs font-medium transition"
            >
              Go to Settings → Telegram Bot
            </Link>
          ) : undefined}
        />
      </div>
    );
  }

  return (
    <div>
      {header}
      <section className="bg-card border border-border rounded-xl p-5">
        {/* AlertRoutingSection is the mode-aware controller: the DELIVERY
            selector on top, then the single-forum table (singleBody) or
            the per-role roster.  The bot-identity block is NOT here — it
            stays on Settings. */}
        <AlertRoutingSection
          botConfig={botConfig}
          canManageAccount={canManageAccount}
          singleBody={<ForumRoutingSection />}
        />
      </section>
    </div>
  );
}
