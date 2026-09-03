/**
 * Ambient mode — what a screen does when nobody is touching it.
 *
 * We already ship a mod called `wall`, described as "a display read from
 * across the room" at 145%. That mod is an admission that one of these
 * screens is bolted to a wall in a yard office and looked at from eight
 * feet away — and until now it did exactly the same thing at 4pm with
 * nobody near it as it did with a dispatcher's nose against it.
 *
 * The mode is deliberately NOT a different page. It is the same page,
 * read from further away: navigation recedes, content grows. That has
 * three properties a bespoke "wall screen" would not have — it works on
 * every route the day it ships, it cannot show stale or wrong data
 * because it is not fetching any, and leaving it is instant because
 * nothing unmounts.
 *
 * The registry below is the other half, and the reason this is a
 * foundation rather than a feature: a page that deserves a purpose-built
 * idle view can register one, and everything else keeps the generic
 * behaviour. Nothing is registered today. That is the point — the
 * mechanism ships first so the next person adds a VIEW, not a system.
 */
import type { ReactNode } from 'react';

/**
 * How much bigger, once idle.
 *
 * 1.35 rather than the `wall` mod's 1.45: this multiplies whatever the
 * person already chose, and the two compose. Somebody on `wall` at 145%
 * lands near 196%, which is a boardroom screen — and that is the right
 * answer for exactly the person who installed `wall`.
 */
export const AMBIENT_SCALE = 1.35;

/**
 * How long untouched before it settles.
 *
 * A constant, not a preference. There is no measurement in this tree
 * sizing it, and a number nobody can justify does not deserve a control
 * — it deserves a comment saying so. Three minutes is long enough that
 * reading a long report does not trigger it and short enough that a
 * screen left after a shift change settles before the next one starts.
 */
export const AMBIENT_AFTER_MS = 3 * 60 * 1000;

/** The events that mean somebody is there. */
export const PRESENCE_EVENTS = [
  'pointermove', 'pointerdown', 'keydown', 'wheel', 'touchstart',
] as const;

/**
 * A purpose-built idle view for one part of the app.
 *
 * Keyed by ROUTE PREFIX, longest match wins, so `/loads/42` can inherit
 * `/loads` without registering twice.
 */
export type AmbientView = () => ReactNode;

/**
 * Empty on purpose, and the emptiness is load-bearing.
 *
 * Shipping a half-built view for one page would make the mode look like
 * it belongs to that page. It belongs to the app: every route gets the
 * generic treatment today, and a page earns a bespoke view when somebody
 * decides what it should actually say.
 */
export const AMBIENT_VIEWS: Readonly<Record<string, AmbientView>> = {};

/**
 * The view for a path, or null for "grow the page you already have".
 *
 * Longest prefix wins so a more specific registration beats a general
 * one however the object happens to be ordered.
 */
export function resolveAmbientView(
  path: string,
  views: Readonly<Record<string, AmbientView>> = AMBIENT_VIEWS,
): AmbientView | null {
  let best: string | null = null;
  for (const prefix of Object.keys(views)) {
    if (path !== prefix && !path.startsWith(prefix.endsWith('/') ? prefix : `${prefix}/`)) continue;
    if (best === null || prefix.length > best.length) best = prefix;
  }
  return best === null ? null : views[best];
}
