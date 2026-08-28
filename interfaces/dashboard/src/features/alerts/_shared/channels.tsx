/**
 * The personal channels a trigger can add, and the words and glyphs they
 * are shown with.
 *
 * Two surfaces render this list and they must agree: the matrix on
 * notification preferences, where each is a column you tick, and the
 * trigger row on the Alerts page, where each is a status you read. They
 * were separate lists — one in TriggerDeliveryMatrix, one as a label map
 * in AlertTriggersSection — which is how "Telegram" ends up meaning one
 * thing in a column header and another in a sentence.
 *
 * The bell is deliberately absent, as it is from the matrix: it is not a
 * choice, so it is not a column and not a status. Both surfaces say so in
 * their own copy instead ("a trigger always appears in the bell").
 *
 * Which of these actually RENDER stays the server's call — both callers
 * intersect this map with `/alerts/triggers/metrics` → `channels`, so
 * retiring a channel server-side removes it from both with no frontend
 * change. The map itself cannot be server-driven: an icon is not a wire
 * value.
 */
import { Mail, MonitorSmartphone, Send } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export interface ChannelMeta {
  key: string;
  label: string;
  icon: LucideIcon;
}

export const CHANNEL_META: ChannelMeta[] = [
  { key: 'telegram_dm', label: 'Telegram', icon: Send },
  { key: 'email', label: 'Email', icon: Mail },
  { key: 'web_push', label: 'Push', icon: MonitorSmartphone },
];

/** The always-on record, which is not one of the choices above. */
export const ALWAYS_CHANNEL = 'in_app';

/** Wire key → the word a person reads, including the bell. */
export const CHANNEL_LABEL: Record<string, string> = {
  [ALWAYS_CHANNEL]: 'Bell',
  ...Object.fromEntries(CHANNEL_META.map((c) => [c.key, c.label])),
};

/**
 * What one channel is doing for one trigger.
 *
 *   `on`      — asked for, and it will arrive
 *   `blocked` — asked for, but nothing will arrive: no verified address,
 *               no subscribed device, or the master switch is off. The
 *               distinction from `off` is the whole point — one is a
 *               choice, the other is a choice that silently isn't kept.
 *   `off`     — not asked for
 */
export type ChannelState = 'on' | 'blocked' | 'off';

export function channelStateFor(
  key: string, configured: string[], reaching: string[] | null,
): ChannelState {
  if (!configured.includes(key)) return 'off';
  // A null `reaching` means the route did not resolve deliverability, so
  // the honest reading is "asked for" rather than "broken".
  if (reaching === null) return 'on';
  return reaching.includes(key) ? 'on' : 'blocked';
}
