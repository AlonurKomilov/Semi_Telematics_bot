# ADR — Telegram bot topology: one identity bot per account; Sub bots are senders

Decided 2026-07-17 (owner + advisor consult). Status: ACCEPTED, shipped.

## Decision

1. **One IDENTITY bot per account.** Registration (`/join`, deep links),
   dashboard Telegram login (BotFather allows one login-widget domain
   per bot), the command surface (43 commands), and the
   `users.telegram_id` binding live on the account's primary bot and
   NEVER move. `infra/bot_registry.py` keys primaries by `account_id`.

2. **Role split happens at the SURFACE layer, not the bot layer.**
   Menus/commands are role-gated per user; alerts split per role
   via `alert_routing_mode = 'per_persona_groups'`
   (`account_persona_groups`, self-serve on Settings → Bot). This is
   the same principle as the dashboard's persona shells: role-shaped
   views over role-neutral shared machinery (docs/architecture/PERSONA.md).

3. **"Sub bot" = an optional per-role SENDER** (`bot_instances`,
   one per `(account_id, persona)`): a role manager attaches their own
   BotFather token and their role's alert group receives posts
   from THAT bot. Sub bots are sender-only — one `/start` info handler,
   an empty command list, no registration, no login.

4. **Fail-open toward the primary.** The pipeline's `_pick_sender`
   uses the Sub bot only when attached AND running; missing/down/
   detached → the primary sends. The owner_admin AGGREGATE cross-post
   always rides the primary (account-level digest, not a role
   surface). A Sub bot can never eat alerts.

5. **Permissions.** Owner/admin manage every Sub bot; a role MANAGER
   (`users.is_manager` on the matching base role) manages exactly their
   own role's (`_may_manage_persona_bot`). The owner_admin
   aggregate bot is owner/admin-only.

## Why NOT one bot per role (rejected)

- Roles are per-user and mutable; small carriers multi-hat. Moving a
  user between bots on role change loses chat history and re-onboards.
- ~9 BotFather setups per account (vs 1), 9× credential
  rotation/leak surface, and BotFather's ~20-bot cap breaks
  multi-account owners.
- One login-widget domain per bot fragments dashboard Telegram login.
- Mixed-audience chats (dispatcher+driver forums) need one identity.
- Throughput doesn't require it: Telegram's binding limit is ~20
  msg/min **per group** (spread by per-role groups, each getting
  its own window from one bot); the per-bot ~30 msg/s ceiling is ~140×
  above the measured peak (13 alerts/min on a 100-vehicle account).

## Scaling ladder (in order)

1. `AIORateLimiter` + one-retry on flood (`alert_send_flood_total`
   metric) — shipped.
2. `per_persona_groups` — one group per role, self-serve.
3. Sub bots — role senders (this ADR).
4. If a single role's group ever saturates ITS 20 msg/min
   window (sustained `dropped` metric): split by FUNCTION (a second
   sender for that role), never by role identity.

## Do not

- Re-key `bot_registry` primaries by anything other than `account_id`.
- Give Sub bots handlers beyond `/start` (identity stays primary).
- Route the owner_admin aggregate through a Sub bot.
- Rebind `users.telegram_id` per Sub bot.

## Per-role topic settings (shipped 2026-07-17, second increment)

Each role row on Settings → Telegram Bot carries "▸ alerts for this
group" (formerly "topics & settings" — renamed so "topic" means only a
real Telegram thread):
per-alert-type **Route to group** and **AI analysis** toggles, filtered
to the types that route to that role (`canonical_types_for_persona` —
the persona mapping is the SSOT, so a role only ever sees its own
types).  Editable by owner/admin or that role's manager (same
`_may_manage_persona_bot` gate as the Sub bot).  Storage is
account_settings: `persona_route.{key}` (role-mode routing on/off —
independent from single-group's `alert_routing.is_active` so a
manager's toggle never rewrites the owner's forum config) and
`forum_ai.{key}` (SHARED with single-group mode by design — AI
inclusion is a per-type editorial choice, not a per-mode one).
Disable semantics mirror single-group: no group post for that type
(resolver returns no targets), per-user DMs still fire.

Role managers reach the page via `can_manage_role_bot` — granted ONLY
through the manager tier (TIER_GRANTS for fleet/safety/dispatcher/hr),
never a base-role seed; the API re-checks `is_manager` + role per
persona regardless (UI reachability ≠ authorization).

Deferred (revisit on demand): real Telegram forum THREADS inside each
role group (today role groups are flat chats with per-type dashboard
controls; the single_group mode keeps its thread-per-alert-type forum).
