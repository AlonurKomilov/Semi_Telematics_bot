# ADR: Alert DM delivery moves to the notifications spine

- **Status:** ACCEPTED (owner decision, 2026-07-24) — rails landed (contract lock, delivery ledger, actions, quiet hours); remaining: the fanout flip + legacy cleanup
- **Owners:** alerting (`capabilities/alerting/`) + notifications (`capabilities/notifications/`)
- **Related:** [notifications.md](notifications.md) (spine architecture), [bot-topology.md](bot-topology.md) (bot vs group split)

## Decision

Telegram **DM** delivery of alerts migrates from the alerting pipeline's own
fanout loop into the notifications spine, alongside email / web push / in-app.
Telegram **group-topic** delivery stays domain-owned in alerting.

The boundary rule that this ADR locks (and `tests/test_layer_boundaries.py`
now enforces):

> **Notifications owns the VERBS** — send, remember-what-was-sent, edit,
> action buttons, defer/quiet-hours, digests.
> **Alerting owns the MEANING** — what "acknowledged" is, when to remind,
> when resolved, who is on shift for which truck, group/topic routing.

The reuse test for any piece of code: *"if a Work Order wants an Approve
button in a DM tomorrow, is this code reused unchanged?"* Yes → it belongs
in notifications. It mentions vehicles/alerts → it belongs in alerting.

## Why

- A DM is **personal delivery** — exactly what the spine exists for. Today
  the DM path duplicates spine concerns with a *second* implementation:
  legacy `alert_prefs` JSONB vs the `notification_pref` matrix, its own
  company filter vs `recipient_filter`, its own quiet-hours (DND) vs
  cadence/digests.
- Group topics are **team-space, domain-stateful** delivery (persona routing,
  Sub-bot senders, ack keyboards edited in place, on-shift mentions,
  aggregate copies). Moving that into the spine would force the spine to
  learn alert semantics and stop being generic — the opposite of clean.
- Unifying the personal path fixes two audit findings for free: the dual
  preference store, and the missing driver-truck scoping on email/push
  (one shared `recipient_filter` predicate for all personal channels).

## Target matrix — MOVES / STAYS / DIES

| Thing | Today | Target |
|---|---|---|
| DM sending loop | `pipeline.py` fanout | ➡️ spine (`TelegramDmChannel`) |
| Message handles ("what did I send where") | `alert_acknowledgments.message_id` | ➡️ spine `notification_deliveries` ledger (delivery_id, channel, recipient, handle, `correlation_key`) |
| Editing sent DMs (reminder counter, ✅ acked, 🟢 resolved) | raw PTB calls in `escalation.py` | ➡️ spine `update_delivery(correlation_key, content)`; channels declare `supports_edit` |
| Ack **buttons** | keyboard built in alerting | ➡️ spine renders `actions=[{id,label}]`, routes callbacks to registered domain handlers (`register_action_handler("alert.ack", fn)`) |
| Ack **semantics** (cascade, status, TTL) | alerting | ✋ stays |
| Re-escalation **policy** (when / how many) | alerting | ✋ stays — sends its reminder *through* `update_delivery` |
| DND quiet-window **deferral** | `dnd_alert_queue` + own flush job | ➡️ spine recipient-level quiet-hours policy (generalizes cadence/digest); critical bypass = severity policy |
| Shift-handoff **content** (summary + PDF) | `dnd.py` | ✋ stays as a digest **renderer** alerting registers; spine decides *when* to flush |
| DM prefs | legacy `alert_prefs` JSONB | ➡️ `notification_pref` matrix ("matrix flip") |
| Company + truck scoping | DM-only filters in alerting | ✋ stays alerting-owned, passed as the existing `recipient_filter` predicate — now applied to **all** personal channels |
| Group topics path (routing_resolver, Sub bots, mentions, aggregate) | alerting | ✋ stays |
| **Dies at the end** | `dnd_alert_queue` table · the DM fanout loop · raw PTB edits for DMs · the `alert_prefs` reader · notifications' reverse import of `alerting.relevance` (done — contract lock) | |

End state: **alerting never touches python-telegram-bot for DMs.** Its DM
vocabulary is `dispatch(content, actions, recipient_filter)`,
`update_delivery(...)`, plus two registered callbacks (action handler,
digest renderer).

Explicit non-goals: alerts stay **out** of the in-app inbox (the Board is
the alert inbox — ack semantics ≠ read semantics); the group-fallback /
critical-mirror orchestration stays in `send_alert` (it just calls the
spine instead of its own loop).

## Rollout steps

| Step | Work | Size | Ships alone? |
|---|---|---|---|
| **Contract lock** ✅ 2026-07-24 | This ADR; boundary rule `capabilities/notifications ⇸ {capabilities.alerting, features}` in `test_layer_boundaries.py`; reverse import removed — role→alert-types now derived from the category registry (`categories_for_source("alert", role)`) | 0.5 d | yes |
| **Delivery ledger + handles** ✅ 2026-07-24 | `notification_deliveries` table; `DeliveryResult` returns handle; `update_delivery()`; per-channel `supports_edit` | 1 d | yes (additive) |
| **Actions + callback routing** ✅ 2026-07-24 | `actions` in content; keyboard render; `notif_act:{correlation_key}:{action}` callback dispatch to registered handlers; alerting registers `alert.ack` | 1 d | yes (additive) |
| **Quiet hours in the spine** ✅ 2026-07-24 | Quiet-window policy + deferral queue on the digest machinery; severity bypass; alerting registers the quiet rule + alert digest renderer; document-attachment rail | 1 d | yes |
| **The fanout flip** | `alert_prefs` JSONB → `notification_pref` rows (idempotent migration, dual-read window); `send_alert` DM fanout → spine call **behind a per-account flag** with parity logging | 1 d | flag-gated |
| **Legacy-path cleanup** | Escalation/resolve edits via `update_delivery`; delete legacy loop + JSONB reader; full test pass | 0.5 d | after parity holds |

Risk control: hottest path in the product → per-account flag (same pattern
as `NOTIFICATIONS_LIVE_DISPATCH`), parity logs during the dual window, the
existing alerting test suite + new contract tests per phase, and the fanout flip
(the only step that touches `send_alert` itself) coordinated with a quiet
deploy moment.

## Landed: quiet hours in the spine (2026-07-24)

Additive; nothing defers until spine alert DMs ship (the fanout flip).
The legacy DND path (`dnd.py` + `dnd_alert_queue`) keeps serving the
live pipeline until the legacy cleanup retires it.

- `notifications/quiet_hours.py` (new) — the policy module: one global
  registered async rule `(account_id, user_id) → is-quiet-now`
  (FAIL-OPEN on none/error — a scheduling bug degrades to a too-eager
  send, never a swallowed one); `severity_bypasses_quiet` (critical cuts
  through); `QUIET_DEFER_CADENCE = "quiet"` rides the existing digest
  queue but is NOT in `DIGEST_CADENCES` (clock flushes never drain it).
- Channel contract: `respects_quiet_hours = True` marks disturbing
  channels (`telegram_dm`, `web_push`); inbox is silent by nature, email
  rides its own cadence system.
- `dispatch()` — immediate sends on disturbing channels defer to the
  quiet queue while the rule says quiet (user recipients only).
- `flush_quiet_deferrals()` + hourly `notification_quiet_flush` job
  (minute 7) — per recipient: still quiet → hold; window ended → ONE
  summary per channel, send-time consent re-check, clear only on
  success. Digest/quiet/daily/hourly all render via the new
  source-renderer registry (`register_digest_renderer(source, fn)`,
  single-source batches only; renderer errors fall back to the generic
  digest so a bad renderer can't wedge a flush).
- Document rail: `NotificationContent`/`Payload.document_bytes` +
  `document_name`; Telegram sends `send_document` (caption-clamped),
  handle `kind="document"`, edits via caption — so a future digest
  renderer can attach the shift PDF.
- `capabilities/alerting/spine_quiet.py` (new, boot-imported) —
  registers (1) the quiet rule delegating to `dnd.is_user_dnd_active`
  (THE SSOT: opt-out tier, assigned schedule, legacy override, role
  work-hours; unknown user → deliver) and (2) the `alert` digest
  renderer ("While you were off shift — N alerts": type-icon counts,
  critical lines spelled out, envelope severity = max). The full
  shift-handoff REPORT (summary + PDF from `get_shift_handoff_data`)
  stays a domain job — it aggregates far more than the deferred queue;
  `dnd_alert_queue` retires with the legacy cleanup.
- Scheduler `_JOB_META` gains the Notifications category (the two
  digest flushes were missing from it too).
- Contract tests: `tests/test_notification_quiet_hours.py` (17).

## Landed: actions + callback routing (2026-07-24)

Additive; the `alert.ack` wire is live but nothing sends spine alert
DMs until the fanout flip, so no user-visible change yet:

- `channels.py` — `NotificationContent.actions`
  (`[{"id","label"}]`): semantic buttons; channels that can't render
  interaction ignore them.
- `notifications/actions.py` (new) — the action registry + routing:
  callback data `notif_act:{correlation_key}:{action_id}` (built only
  under Telegram's 64-byte cap, parsed with rpartition so keys may
  contain colons); handler key = `{source}.{action_id}` where source =
  the correlation-key namespace (mirrors category namespacing);
  `ActionContext` carries account, key, presser, the pressed message's
  handle + text; `handle_action_callback` is the PTB entry (account from
  `bot_data["account_id"]`, polite answers on unknown/crash).
- `telegram.py` — `render_telegram` builds the inline keyboard from
  `content.actions` + the meta correlation_key (stamped by
  dispatch/notify_user); missing key or over-long button drops the row
  (no dead buttons). Empty-title contents render body-only (edit
  footers).
- `interfaces/bot/callbacks/__init__.py` — `_router.prefix("notif_act:")`
  delegates to the spine dispatcher (interfaces→capabilities, legal
  direction).
- `capabilities/alerting/spine_actions.py` (new, boot-imported) —
  registers `alert.ack`: history-level ack first then per-delivery
  fallback (the dashboard's `_ack_one` order), then
  `update_delivery(clear=True)` appends "✅ Acknowledged by X" to every
  recorded copy; a failed cosmetic edit never undoes the ack. Also
  exports the flip's contract: `correlation_key_for_history()` +
  `ACK_ACTION`.
- Contract tests: `tests/test_notification_actions.py` (17).

## Landed: delivery ledger + edit handles (2026-07-24)

All additive — nothing writes until a caller passes `correlation_key`:

- `channels.py` — `DeliveryResult.handle` (channel-opaque edit-address,
  empty on failure / immutable channels) + the `supports_edit` /
  `edit(recipient, handle, payload)` protocol extension (getattr-read,
  like `intrinsic`, so existing channels need no change).
- `telegram.py` — `_send` populates the handle
  (`chat_id`/`message_id`/`kind`, `thread_id` for topics); new shared
  `_edit` leaf picks text-vs-caption by `kind`, treats Telegram's
  "message is not modified" as success, reuses the flood-retry primitive.
  Both Telegram channels declare `supports_edit` and route `edit()`.
- `service.py` — `dispatch()`/`notify_user()` accept `correlation_key`
  and record each successful immediate send with a handle into the
  ledger (best-effort — a ledger failure never sinks a delivery); new
  `update_delivery(db, account_id, correlation_key, content, *,
  channels, clear)` re-renders and edits every recorded row, skipping
  non-editable channels; `clear=True` drops rows only when every edit
  succeeded (partial failure keeps them for retry).
- Storage: `NotificationDeliveriesMixin`
  (`adapters/storage/notification_deliveries.py`, registered on
  `Database`), table in `platform_schema.py` + idempotent
  `migrate_notification_deliveries` for existing DBs, retention target
  `notifications.deliveries` (30 d — outlives the longest re-escalation
  schedule; sources clear their own rows on an event's final edit).
- Contract tests: `tests/test_notification_deliveries.py` (14 — handle
  population, edit-verb selection, not-modified-as-success, ledger
  recording rules, update/skip/clear semantics), all on fakes.

## Landed: contract lock (2026-07-24)

- `capabilities/notifications/categories.py` — new `categories_for_source()`:
  registry-derived "which alert types can this role see", order-stable
  (registration order = `ALERT_TYPE_REQUIRED_PERM` order).
- `capabilities/notifications/router.py` — the two `/prefs/{channel}`
  handlers use the registry derivation instead of importing
  `capabilities.alerting.relevance`. Behavior-identical: the registered
  `alert.*` categories' `audience` closures ARE
  `role_can_receive_alert(role, type)`, registered by alerting at boot
  (`capabilities/alerting/notification_categories.py`, imported from the
  package `__init__`, which the API app always loads).
- `tests/test_layer_boundaries.py` — the spine's source-blindness is now a
  parametrized rule, not a convention.
