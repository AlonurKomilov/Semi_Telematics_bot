# Notifications architecture — multi-channel delivery

> **Status: SHIPPED — this document is the SSOT for the notification
> spine.** The vocabulary, domain model, schema and channel contract
> below are what `capabilities/notifications/` implements today (26
> modules, ~5,650 lines; the spine landed 2026-07-20 and alerting was
> rewired onto it 2026-07-27). Live: five registered channels —
> `telegram_dm`, `telegram_topic`, `email`, `web_push`, `in_app` — the
> `notification_pref` × `notification_channel` matrix, the digest and
> quiet-hours queue, the in-app inbox + notification center, the
> delivery (edit-address) ledger, web push with its own VAPID keypair and
> anti-SSRF endpoint gate, and the Group-delivery admin surface.
>
> **Designed here but NOT built:** the SMS channel (no `SmsChannel`, no
> OTP/STOP path), a per-attempt delivery-status log (§10), shared
> `recipient_type='topic'`/`'account'` rows in the matrix (§4/§6 — the
> shared side is resolved per alert, not stored as prefs), per-vehicle
> scoping for non-Telegram recipients (§9a), and the non-alert half of
> the audience sweep (§9d). Text marked *(design, unbuilt)* is the only
> forward-looking material here; §2 records the pre-spine starting state
> for history, and §15/§17/§17b/§18 are the decision record.
>
> **Section numbers are a contract.** Eight shipped files cite this
> document — `capabilities/notifications/{__init__,channels,service}.py`,
> `adapters/storage/{notification_prefs,platform_schema,platform_migrations}.py`,
> `capabilities/platform/billing/notifications.py`,
> `interfaces/dashboard/src/features/alerts/NotificationsPanel.tsx` —
> and they name §4, §5, §6, §7, §9, §9d and §13a. Correct a section in
> place; never renumber one.

## 0. TL;DR

- **Three layers, never collapsed:** **Alert** (the event, per-account) →
  **Notification** (a delivery to a recipient, per-recipient preference)
  → **Channel** (the transport adapter). A channel is a property of
  *delivery*, not of the alert.
- Your instinct is right: `Alerts → Notifications → [channels]`, **not**
  `Alerts → [channels]`.
- **ONE gate — the topbar bell.** The avatar-menu "My Notifications"
  entry is gone: two doors into one feature was the confusion we were
  killing. What shipped is not an Alerts sub-tab, though — the bell opens
  a multi-source inbox and its ⚙ deep-links a top-level page,
  `/notifications/preferences`, because notifications are a cross-source
  PERSONAL concern (alerts, team, applications, AI, KPI, system), not an
  Alerts sub-feature. Preferences are therefore NOT gated on
  `can_alerts_*`: every authenticated user has them, and each category's
  `audience` decides what the page lists. The alert BOARD stays at
  `/alerts` behind `can_alerts_*`.
- **Per-user vs per-account is the RECIPIENT axis** inside the
  notification layer, not the Alerts/Notifications boundary. Two
  recipient scopes: **personal** (per-user: my Telegram DM, email, web
  push, in-app inbox) and **shared** (per-account/admin: team Telegram
  topic, ops distro).
- **The code is a cross-cutting capability, `capabilities/notifications/`**
  (channel registry + preference matrix + dispatch). It shipped as
  module-level functions, not a service class: `dispatch()` for
  account-wide BROADCAST categories, `notify_user()` for TARGETED ones.
  Alerts were the first event source; billing, team, applications, AI and
  KPI followed, and the UI followed them out of Alerts.
- **The one hard requirement your data forces: digest/batching.** 668
  alerts in 7 days (513 safety events) means per-alert email/SMS is
  unusable — cadence (immediate | hourly | daily) is a first-class
  preference field, not a v2 nicety.

## 1. Vocabulary (the SSOT — use these words in code + UI)

| Term | Definition | Scope | Example |
|---|---|---|---|
| **Alert** | An event the alerting pipeline raised | per-**account** | "Fault SPN 100 on truck 22" |
| **Alert type** | The category of alert | — | `faults, camera, health, fuel, events, geofence, parking` (canonical keys, `ALERT_TYPE_REQUIRED_PERM` order) + `maintenance` (routes to a group but registers no notification category, so it stays Telegram-only — §9a); `fault`/`event`/`doc_expiry` are pipeline-side aliases normalised by `_PIPELINE_TO_ROUTE_KEY` |
| **Channel** | A transport adapter | — | `telegram_dm, telegram_topic, email, web_push, in_app` (`sms` designed, not built) |
| **Recipient** | Who a notification is delivered to | user *or* account-destination | a user; a Telegram topic |
| **Preference** | A rule: recipient wants alert-type on channel (+ cadence) | per-recipient | "me · fuel · email · daily" |
| **Notification** | One rendered delivery of an alert to one recipient via one channel | — | the email that got sent |
| **Delivery** | The remembered edit-address of a message that WAS sent, so a source can update it later | — | `DeliveryResult(ok, error, provider_ref, handle)`; the ledger row keeps `handle` = Telegram chat_id + message_id |

No `sent / failed / suppressed` status vocabulary was built: the outcome
is the boolean `ok` plus a free-text `error`, and only a SUCCESSFUL send
that carried a `correlation_key` and returned a `handle` leaves a row
(§10). Severity is its own SSOT tuple — `("info", "warning", "critical")`.

Naming fix that killed the original confusion: **"Alerts"** stays the
board; the personal settings surface left the avatar menu and became
`/notifications/preferences`. Its shipped page header reads
**"Notifications"** — the "preferences" meaning lives in the route and the
bell's gear — and the two are unambiguous because they are now two
different doors, not two names for one page.

## 2. Starting state (pre-spine, accurate as of 2026-07-20)

- **Events:** `alert_history` — one row per account × alert_type × vehicle
  × **alert_subkey** (`UNIQUE(account_id, alert_type, vehicle_id,
  alert_subkey)`, migration `033_alert_history_subkey`); the subkey is what
  lets two distinct faults on one truck stay two rows. Raised by
  `capabilities/alerting/pipeline.py`. Account-scoped, role-relevance via
  `capabilities/alerting/persona_mapping.py` + `relevance.py`. (Still true
  today — the event layer was not what changed.)
- **Personal preferences:** the per-user **columns** `users.alert_faults /
  alert_health / alert_fuel / alert_geofence / alert_events /
  alert_parking / alert_camera`, plus `alerts_on` (master switch) and
  `alert_resolve_receipts` (🟢 receipts). A `users.alert_prefs` JSONB was
  added alongside them, but the columns were never dropped. Channel-blind:
  implicitly **Telegram DM only**. The `ai_*` keys ride the same store
  (per-type AI-analysis inclusion — a separate concern, see §11).
  **Today:** `notification_pref` is the delivery SSOT; the legacy columns
  survive as a write-mirrored cache (`prefs_mirror.mirror_alert_prefs_to_matrix`,
  called from `PUT /user/me/alerts`, the bot's per-type toggle and master
  switch, and `adapters/storage/users.py`), and the `alert_prefs` JSONB has
  no remaining reader.
- **Shared routing (already built, mature):** `accounts.alert_routing_mode`
  = `per_persona_groups` (each ROLE gets its own Telegram group) or legacy
  `single_group` (one group, topic threads). `persona_mapping.py` routes
  each alert type to a role group (fuel→Dispatcher, events/camera→Safety,
  faults/health/maintenance→Fleet, documents→HR, system→Owner/Admin);
  Owner/Admin aggregate fires on every CRITICAL.
  `routing_resolver.resolve_alert_targets()` returns the target list. This
  IS the "shared destination" recipient scope — reuse it, don't rebuild.
- **Channels then:** **Telegram only** (DM + topics), via the per-account
  bot registry (`infra/bot_registry.py`).
- **Email then:** `capabilities/email/` existed (Resend + SMTP transports;
  lifecycle/auth/application emails) — **not wired to alerts.** It is now
  the transport underneath `EmailChannel`
  (`capabilities/notifications/email.py` calls `capabilities.email.send_email`).
- **Web front:** the `/alerts` board + the topbar bell count.

**Gaps for multi-channel:** the legacy prefs had no channel dimension, no
shared-recipient support, no cadence. That is what the spine below fixed.

**Everything in this section is the state the spine REPLACED.** For what
runs today: §5 (the five registered channels), §6 (the matrix), §9/§9a
(dispatch + the alert seam), §9b/§9c (the in-app inbox and centre).

## 3. Target domain model

```
  Alert (event)                         per-account, raised once
     │
     ▼
  notifications.dispatch(db, account_id, content)   cross-cutting
     │  1. resolve recipients+prefs  (who wants this type, on which channel, cadence)
     │  2. for each (recipient, channel): render + enqueue
     │  3. immediate → send now;  digest → accumulate into a bucket
     ▼
  channel.send(recipient, payload) → DeliveryResult(ok, error, handle)
```

Three layers, each with one job:

1. **Alert layer** — unchanged. Raises typed, account-scoped events. It
   must NOT know about channels or users' preferences.
2. **Notification layer** — resolves *who + how*, renders, batches,
   dispatches, records delivery. Channel-agnostic core.
3. **Channel layer** — pluggable adapters behind one interface.

## 4. Recipient scopes (resolves the per-user vs per-account point)

Delivery keys on **(recipient, channel, category)** — recipients come
in two kinds, both inside the notification layer:

- **Personal** (`recipient_type='user'`): my Telegram DM, my email, my
  web-push, my in-app inbox (my SMS, if that channel is ever built). Each
  user owns their own toggles. → the **Notification preferences** page
  (`/notifications/preferences`).
- **Shared** (`recipient_type='account'` / `'topic'`): a team Telegram
  topic, a shared ops email distro, a webhook. Admin-controlled. → the
  **Group delivery** tab (`/alerts/group-delivery`, the re-homed Forum
  Routing). *(design, unbuilt as prefs)*: no code writes
  `recipient_type='topic'` rows into `notification_pref` yet — shared
  destinations are resolved per alert by `resolve_alert_targets` into a
  `DeliveryPlan`, and only the delivery ledger carries them, keyed by the
  persona slug (`fleet`, `safety`) as `recipient_id`.

**Why this matters:** email (and SMS) are BOTH — a personal inbox *and* a
shared distro. If the schema assumed "notifications = per user" it would
break the day someone wants alerts in a shared ops mailbox. The recipient
abstraction avoids that.

## 5. Channel adapter contract (the dev extension point)

Every channel is one adapter, registered like this codebase's other
registries (tools, ImportTargets, artifacts, undo recipes):

```python
class Channel(Protocol):
    key: str                    # 'telegram_dm' | 'telegram_topic' | 'email'
                                # | 'web_push' | 'in_app'   ('sms' unbuilt)
    personal: bool              # per-user address vs shared destination
    def render(recipient: Recipient, content: NotificationContent) -> Payload
        # channel-specific: escaped Telegram HTML / subject+text+html /
        # short-form push text
    def send(recipient: Recipient, payload: Payload) -> Awaitable[DeliveryResult]
    # optional, read via getattr so existing channels need no change:
    #   intrinsic = True             — no address to connect/verify (in_app)
    #   supports_edit = True         + async def edit(recipient, handle, payload)
    #   respects_quiet_hours = True  — defer non-critical during quiet hours
    #   accepts_sender_hint = True   — send(..., sender_hint=…), persona Sub bots
```

There is **no `connect()` and no `resolve_address()`** on the protocol, and
no `ConnectionResult` type. Connection lifecycle is its own framework-free
module (`lifecycle.py`, with the HTTP skin in `router.py`) over the
`notification_channel` table; the address is resolved by the subscriber
query (`get_notification_subscribers`) and carried on `Recipient.address`
before a channel is called. `render` takes no locale and no
`alert_or_digest` union — a digest becomes an ordinary
`NotificationContent` first (§9). No rate-limit or cost-per-message hooks
exist.

**Adding SMS = write `SmsChannel` + `register_channel(SmsChannel())`**
*(design, unbuilt — nothing named `SmsChannel` exists in the repo).*
The preference matrix, dispatch engine, and UI (which iterates registered
channels) pick it up automatically. Zero changes to the Alert layer.

Telegram is the **first two registered channels**, wrapping the existing
pipeline delivery — a refactor, not a rewrite. **These are two SEPARATE
channels, distinct code paths — never merge them:**

| Channel | Path today | Recipient | Address | Owner control |
|---|---|---|---|---|
| **`telegram_dm`** | the **per-subscriber DM fanout**, through the spine (`_spine_dm_fanout` → `dispatch(channels=("telegram_dm",))`; matrix rows via `get_notification_subscribers`, effective-permission gate `relevance.filter_users_by_alert_access`) | **personal** (`user`) | user's `telegram_id` | each USER (Notification preferences) |
| **`telegram_topic`** | **`post_alert_to_topic` / `resolve_alert_targets`** (per-persona groups), executed as a `DeliveryPlan` | **shared** (`topic`/`account`) | group `chat_id` + `message_thread_id` | ADMIN (Group delivery) |

`post_alert_to_topic()` returns True/False to tell the caller whether to
also run the DM fanout — that boolean IS the seam between the two
channels. Each side is wrapped independently: the personal "bot → user DM"
path and the shared "bot → group topic" routing stay fully separate, with
different recipient types, addresses, and owners.

## 6. Preference model — the schema decision

The shipped shape: a relational **`notification_pref`** table
(per-recipient, per-channel, per-category, + cadence):

```
notification_pref(
  account_id,
  recipient_type   'user' | 'account' | 'topic',
  recipient_id,                       -- user_id / topic id / distro id
  channel          'telegram_dm' | 'telegram_topic' | 'email'
                   | 'web_push' | 'in_app',   -- 'sms' reserved, unbuilt
  category,                           -- source-namespaced: 'alert.faults'
                                      -- | 'team.invite_accepted' | '*'
  enabled          INTEGER NOT NULL DEFAULT 1,   -- 0/1, not a native bool
  cadence          'immediate' | 'hourly' | 'daily'   DEFAULT 'immediate',
  updated_at       TEXT NOT NULL DEFAULT '',     -- last write, ISO-8601
  PRIMARY KEY (account_id, recipient_type, recipient_id, channel, category)
)
```

The column shipped as `alert_type` and was renamed by
`migrate_notification_category_rename`, which renamed it on BOTH
`notification_pref` and `notification_digest_queue` and rewrote every value
to `'alert.' || category WHERE category <> '*'`. A category key is
`<source>.<key>`; six source namespaces ship today — `alert`, `system`,
`team`, `applications`, `ai`, `kpi` — so the matrix is not alert-specific.

Plus a per-channel **connection** table (address + verified state), since
"is my email connected" is separate from "do I want fuel alerts on it":

```
notification_channel(
  account_id, recipient_type, recipient_id, channel,
  address,                 -- email / phone / push-sub / telegram_id
  verified_at,             -- '' = unverified
  enabled_master,          -- the per-channel master switch
  updated_at,
  PRIMARY KEY (account_id, recipient_type, recipient_id, channel)
)
```

*(Known code inconsistency, not a doc claim: upgraded installs also carry a
`provenance` column added by `migrate_seed_application_notification_channels`,
while the fresh-install CREATE TABLE in `platform_schema.py` omits it. Fix
the schema, then delete this note.)*

**What it looks like with real rows** (Allen = user 7, account 1) — one
table expresses *every* channel × type × cadence × recipient:

```
account │ recipient_type │ recipient_id │ channel        │ category             │ enabled │ cadence
────────┼────────────────┼──────────────┼────────────────┼──────────────────────┼─────────┼──────────
   1    │ user           │ 7            │ telegram_dm    │ alert.faults         │  true   │ immediate   ← Allen: faults to his DM, now
   1    │ user           │ 7            │ telegram_dm    │ alert.fuel           │  true   │ immediate
   1    │ user           │ 7            │ email          │ alert.fuel           │  true   │ daily       ← fuel also by email, once a day
   1    │ user           │ 7            │ email          │ alert.faults         │  false  │ daily       ← but NOT faults by email
   1    │ user           │ 7            │ web_push       │ alert.events         │  true   │ immediate   ← safety events as a browser pop-up
   1    │ user           │ 7            │ in_app         │ team.invite_accepted │  true   │ immediate   ← a NON-alert source, same table
   1    │ topic          │ fleet        │ telegram_topic │ alert.faults         │  true   │ immediate   ← SHARED (designed shape — see §4)
   1    │ topic          │ safety       │ telegram_topic │ alert.events         │  true   │ immediate   ← SHARED (designed shape — see §4)
```

Read a row as a sentence: *"Allen wants **fuel** alerts on **email**, batched **daily**."*
A category is namespaced by its SOURCE, so the same table carries
`team.invite_accepted` or `ai.action_executed` beside the alert rows.
Adding SMS would be new rows with `channel='sms'` — no schema change, no
new columns (the channel itself is unbuilt). Alongside it, the
`notification_channel` table holds the *connection* (is my email verified?
my push subscription for this device?) separate from these per-type
toggles.

**Interim option NOT taken (kept for the record):** nesting the existing
JSONB — `users.alert_prefs = {telegram_dm: {faults:true,…}, email:{…}}` —
was the lighter migration, but had no shared recipients, no cadence column,
and no way to query "who wants fuel on email." The relational shape shipped
(§17b Decision 1).

## 7. Channel connection lifecycle

Each channel has a connect step separate from per-type toggles:

- **Telegram DM** — link `telegram_id` (already exists via bot start).
- **Email** — the channel verifies its **own** address, not the login
  email (a user may want alerts in a different inbox): stateless HMAC
  tokens (`tokens.py` — the signature is the proof, no token table) plus
  `lifecycle.py`, with `capabilities/email.send_email` as the transport.
  Unsubscribe (CAN-SPAM) shipped as an RFC 8058 `List-Unsubscribe`
  One-Click header plus a GET confirm page that does NOT mutate — it POSTs
  to `/notifications/unsubscribe-confirmed`, so a mail-scanner prefetch
  cannot silently unsubscribe a fleet from safety alerts.
- **Web Push** — browser permission grant; **per-device** (a user can have
  push on laptop but not phone) → push subscriptions are sub-entities of
  the user, stored per-subscription (`push_subscriptions`, cap 10 devices
  per user, https-only globally-routable endpoint gate).
- **In-app** — `intrinsic = True`: no address, no connect step, never
  greyed; broadcasts resolve OPT-OUT rather than opt-in (§9b).
- **SMS *(design, unbuilt)*** — phone OTP; **STOP/opt-in** compliance
  (TCPA); costs money → rate/quota limits.

UI is always: **connect the channel → then toggle which alert types flow
to it** (a disabled/greyed type list until connected).

## 8. Delivery cadence & digest (REQUIRED, not optional)

Given ~668 alerts/7d (513 safety events), per-alert email/SMS is
unusable. Cadence is a first-class preference:

- `immediate` — send now (Telegram default; fine for a chat channel).
- `hourly` / `daily` **digest** — accumulate matching alerts into a
  bucket, render ONE summary per period ("12 fuel + 40 safety in the last
  hour"), send once. Email's connect-time default is `daily`
  (`_CHANNEL_DEFAULT_CADENCE`); web push defaults to `immediate`.
- `quiet` — an INTERNAL fourth value a user never chooses
  (`QUIET_DEFER_CADENCE`, deliberately NOT a member of `DIGEST_CADENCES`).
  An `immediate` send on a channel declaring `respects_quiet_hours = True`
  (`telegram_dm`, `web_push`) is re-labelled `quiet` and parked in the same
  queue while the recipient's registered quiet-hours rule says quiet;
  CRITICAL bypasses it, and it fails open when no provider is registered.
- Digest buckets are scheduler jobs in the APScheduler catalog:
  `notification_digest_hourly` (:05), `notification_digest_daily`
  (13:00 UTC) and `notification_quiet_flush` (:07) — the last drains the
  `quiet` rows per recipient's window rather than by a clock, so the
  hourly/daily flushes never touch them. Dedup/hysteresis stays in the
  alert pipeline (unchanged); the digest just groups what survived dedup.

## 9. Dispatch engine flow

```
dispatch(db, account_id, content)              # BROADCAST categories
  for channel in (channels or list_channels()):
     subs = db.get_notification_subscribers(account_id, content.category,
                                            channel.key)
        # the connection check IS this query: it joins notification_pref ×
        # notification_channel and returns only rows with
        # enabled_master = 1, verified_at <> '', address <> ''.
        # Intrinsic channels (in_app) skip the join and resolve OPT-OUT
        # via get_optout_subscribers().
     subs = _filter_recipients(db, subs, category.audience, recipient_filter)
     for s in subs:
        cadence = s.cadence
        if cadence == 'immediate' and channel.respects_quiet_hours and quiet:
           cadence = 'quiet'                   # QUIET_DEFER_CADENCE
        if cadence != 'immediate':
           db.enqueue_digest_item(...);  continue
        rcpt    = Recipient(account_id, s.recipient_type, s.recipient_id,
                            s.address)
        payload = channel.render(rcpt, content)
        res     = await channel.send(rcpt, payload)
        _record_delivery(...)     # ONLY when correlation_key and res.handle

notify_user(db, account_id, user_id, content)  # TARGETED categories:
                                               # opt-OUT, immediate only

flush_digests(db, cadence)                     # 'hourly' | 'daily', scheduled
  for each (recipient, channel) group in db.fetch_due_digest_items(cadence):
     content = _render_digest(items, cadence)  # source-registered renderer,
                                               # else build_digest_content()
     payload = channel.render(rcpt, content)   # the SAME render path
     send;  db.clear_digest_items(ids)         # cleared only after success

flush_quiet_deferrals(db)                      # the 'quiet' rows, own job
```

The pipeline calls `notifications.dispatch(...)` / `notify_user(...)` — it
never talks to a channel directly. Nothing named `resolve_prefs`,
`channel.connected`, `channel.resolve_address`, `channel.render_digest` or
`log_delivery` exists: connection is a JOIN, the address is on the
`Recipient`, and a digest is rendered through each channel's ordinary
`render()` because it is just another `NotificationContent`.

### 9a. Live wiring — the alert seam (N2)

`capabilities/alerting/pipeline.py::send_alert` is the universal alert
delivery path, and **Telegram rides the spine too**: the personal fanout
goes through `_spine_dm_fanout` → `dispatch(channels=("telegram_dm",))`,
and group topics ride a `DeliveryPlan` executed by `plan.deliver()` through
the `telegram_topic` channel. Immediately after an alert clears every
suppression/mute gate — and **before** the group-vs-DM routing fork, so
forum-routed accounts are covered too — it ALSO calls
`dispatch_new_channels(...)`, which hands the same semantic event to
`dispatch()` restricted to the `("email", "web_push")` channels. The two
calls are separate, so nothing is double-sent. Alerting no longer speaks
any transport: the CI layer guard forbids `capabilities/alerting` from
importing `telegram`, and forbids `capabilities/notifications` from
importing `capabilities.alerting` or `features`.

- **Direction is one-way** (`alerting → notifications`); the layer guard
  in `test_layer_boundaries.py` enforces it.
- **Flag:** `NOTIFICATIONS_LIVE_DISPATCH` (default off) gates ONLY the
  email/web-push seam. Inert until an account is switched on; the Telegram
  DM and topic paths ride the spine unconditionally and are untouched by it.
- **Non-fatal:** any Notifications failure is logged and swallowed so an
  alert Telegram is about to deliver can never be sunk by an email/push
  error.
- `alert_text` is Telegram HTML; `dispatch_new_channels` strips tags +
  unescapes entities so `NotificationContent.body` is RAW plain text
  (each channel escapes exactly once at render).
- **Category is canonical + must exist.** The pipeline's verbose
  `alert_type` ("fault") is mapped through `_PIPELINE_TO_ROUTE_KEY` to the
  registry key ("faults"), and delivery is skipped when no category is
  registered — an alert type without a real category + audience rule
  (doc-expiry, scorecard, system…) stays Telegram-only rather than fanning
  out ungated.
- **Company scope — per-user predicate (parity with Telegram).**
  `dispatch()` takes an optional `recipient_filter: (user_id, role) -> keep`
  — a caller-supplied visibility gate applied to every user-type recipient
  in the same pass as the role `audience` gate (before digest enqueue, so
  both cadences are covered). `dispatch_new_channels` builds it from
  `company_scope.user_sees_company` + the alert's `co` and passes it in, so
  a user restricted to Company A is dropped for a Company B alert **without
  the notification core ever importing alerting or learning what a "company"
  is** (the one-way boundary holds; the param is domain-neutral). Fail-open:
  no `co`, no per-user scoping, a scope-load error, or a predicate that
  raises all deliver — exactly like the Telegram path. This replaced an
  earlier account-wide fail-closed hold-back (advisor decision, Option A).
- **Known gap (deferred):** even within a single company, recipient
  selection is role/audience-scoped via the category `audience` but NOT yet
  per-driver-truck scoped the way the Telegram DM path is — a driver opting
  email into `alert.faults` would hear all trucks. Per-vehicle scoping is a
  follow-up (same vehicle-scope contract as the AI copilot write tools).

### 9b. In-app inbox — the bell as a multi-source feed (N5)

The bell dropdown is a true notification inbox, not just an alert glance.
Design (advisor-endorsed): **intrinsic channel + dual source**.

- **`in_app` channel** (`capabilities/notifications/inapp.py`): a
  registered Channel whose `send()` PERSISTS a `notification_inbox` row
  (account_id, user_id, category, source, severity, title, body, url,
  meta, created_at, read_at) instead of transmitting. Because it's a
  channel, every row has already passed the same dispatch()/notify_user()
  gates as a real delivery — category audience, the caller's
  recipient_filter scoping, per-user mutes. No parallel write path.
- **`intrinsic = True`** (documented on the Channel protocol, read via
  getattr): no address to connect/verify → always available. notify_user
  skips the connection check; dispatch resolves broadcast recipients
  OPT-OUT (`get_optout_subscribers`: all active users minus explicit
  mutes, specific-beats-star) because a passive record nobody pre-enables
  would stay empty forever. Cadence always immediate.
- **Dual-source bell**: alerts stay on `/alerts/pending` (ack/occurrence
  semantics can't be honestly mirrored in an inbox row — never
  double-store); the inbox holds the non-alert sources. The alerting
  seam passes `channels=("email","web_push")` explicitly so alerts never
  write inbox rows. `list_channels()` carries a footgun note: a
  `channels=None` fan-out includes in_app by design.
- **API**: GET `/notifications/inbox` (newest-first, source filter,
  keyset `before_id`, + true unread count), POST `/inbox/read` (scoped to
  own rows), POST `/inbox/read-all`.
- **Bell UI**: source tabs — All (merged, default) | Alerts
  (permission-gated, unchanged feed + an All/Critical SUB-filter + staged
  bulk-ack) | Applications (permission-gated) | Activity (everything else
  non-system) | System.
  Inbox rows: unread dot, read rows dimmed, click = mark-read + navigate
  the notice's relative url. Footer per tab: Acknowledge all (staged) vs
  Mark all read (instant — read state is trivially reversible).
  Per-tab counts are page-approximate; the BADGE = pending_alerts +
  server-true inbox unread, summed client-side (two stores, two
  lifecycles, the bell just adds numbers).
- **Prefs**: `in_app` is the first column of the Account-activity grid —
  never greyed (intrinsic = always ready); muting it hides a category
  from the bell. `AccountActivityChannel` Literal includes it.
- **Retention**: `notifications.inbox` registered with the data_lifecycle
  hub, 60d window, pruned regardless of read state (glance, not archive).

### 9c. Reference-pattern adoptions (N6)

Borrowed from the reviewed reference overlay (owner-picked shortlist):

- **All tab** — the bell's DEFAULT tab interleaves alert rows + inbox
  notices newest-first (client-side merge of the two stores; each row
  keeps its own semantics — ack vs read). All-pill count = loaded alert
  rows + the server-true inbox unread.
- **Notification center** — `/notifications` is now a real page (was a
  redirect): browsable 60-day history with per-source filters
  (All | Team | AI | System — "Team" not "Activity" to avoid colliding
  with the bell's coarse team+ai Activity bucket), keyset "Load older",
  mark-all-read, links to Preferences and (for alert roles) the Alerts
  board. Bell footers link "See all" here. Monotonic request token guards
  the paginated loader against stale-response races.
- **Context chip + actor-led titles** — notices carry `meta.context`
  ("Team", "AI"), surfaced as a small object chip on inbox rows (GET
  /inbox returns `context`, safe-parsed). The invite notice is retitled
  actor-led: "<name> joined your team".
- **AI as a source** — `ai.action_executed` (TARGETED): every executed AI
  proposal leaves a record in the APPROVER's inbox, `channels=["in_app"]`
  ONLY (they were on-screen — the value is the durable trail, not an
  interruption). Registered at `capabilities/ai/notifications.py`; called
  from `execute_approved_action` after finalize+audit, non-fatal.
  `NOTIFICATIONS_AI_EVENTS` defaults **ON** (deliberate deviation from
  the default-off convention: writes only to the actor's own inbox; the
  env var is a kill switch, not a rollout gate).
- Deliberately NOT adopted: per-row dismiss-X (second lifecycle verb,
  hover-dependent micro-target — cab-tablet rule), expandable rows +
  inline row actions (deferred until a source needs them).

### 9d. Audience gate — closed for alerts, still static elsewhere

**Closed 2026-07-27 (owner decision) for the ALERT path.** Personal alert
delivery resolves the per-account **effective** `FeatureSet` via
`get_user_permissions`, memoized per (account, role, manager,
primary-owner) tier in `relevance._effective_fs` and applied by
`filter_users_by_alert_access` / `alert_types_for_user`; the alerting
caller passes the resulting allowed-id gate into `dispatch()`.
`role_can_receive_alert` is now only the fail-open-per-tier fallback.
Permissions are the single truth for RECEIVING too: revoking a feature
stops its personal DMs, granting it starts them.

**Still open — the rest of the sweep, and it did NOT happen the planned
way.** `NotificationCategory.audience` is unchanged: still
`Callable[[str], bool] | None` over a ROLE string, and `_filter_recipients`
still calls `audience(role)`. So the non-alert categories (billing's
`_billing_audience`) and the prefs-page listing gate still resolve STATIC
role defaults (`ROLE_PERMISSIONS` / `get_permissions`) — per-account
Role-Permissions overrides and module-disablement masking are not consulted
there. Risk is same-tenant + informational only (the real billing/alerts
pages stay account-aware-gated). The advisor's 2026-07-24 plan still stands
for that remainder: change `audience` to a sync predicate over an effective
`FeatureSet` (`audience(perms) -> bool`), resolve `get_user_permissions`
centrally in `_filter_recipients` once per distinct (role, tier) combo,
fall back to static on resolution failure. Never patch billing alone.

## 10. Delivery log / observability

`notification_deliveries(id, account_id, channel, recipient_type,
recipient_id, category, correlation_key, handle, created_at)` — the
**edit-address ledger**, not a status log. `handle` is the channel's opaque
JSON edit address (Telegram: chat_id / message_id / kind) and
`correlation_key` is the source's stable event key (e.g.
`alert:{history_id}`), so a source can later EDIT a message it already sent
— ack stamps, "reminder 2/4" — via `update_delivery()` without speaking the
transport's dialect. Index `idx_notification_deliveries_corr(account_id,
correlation_key)`. A row is written ONLY for a send that succeeded AND
returned a handle AND whose caller passed a `correlation_key`.

**It therefore does NOT answer "why didn't I get my alert?"** — there is no
`status`, `sent_at` or `error` column and no row for a failed or suppressed
delivery; failures are logged, never rowed. A per-attempt failure log, and
the per-user delivery history it would power, is *(design, unbuilt)*.
Retention is live: registered with the data_lifecycle hub as
`notifications.deliveries` (30 days), beside `notifications.inbox`
(60 days) and `notifications.digest_queue` (14 days).

## 11. Separate concern to keep OUT: `ai_*` prefs

The legacy per-user store also holds `ai_fault`/`ai_health`/… — these are
**"include AI analysis in this alert,"** NOT a channel. They're an
alert-*content* toggle, orthogonal to delivery. Keep them on the alert/
content side; do not fold them into the channel matrix. (Flag: name them
clearly, e.g. `ai_analysis.<type>`, to end the overload.)

## 12. Decoupling principle (ages best)

The **code** is a cross-cutting capability (`capabilities/notifications/`)
that any event source calls, and the UI followed the same logic: once the
second source arrived, notifications got their own door. Six source
namespaces register categories today — `alert`, `system`, `team`,
`applications`, `ai`, `kpi` — so "an application arrived", "an AI action
was executed" and "your incentive run is ready" ride the same dispatch as
a fault. Wiring channels *into* Alerts would have forced a rebuild at the
first non-alert source; it did not.

## 13. UI / information architecture

**ONE gate: the topbar bell.** No second entry anywhere — but the bell is a
NOTIFICATIONS door, not an Alerts sub-tab.

```
🔔 topbar bell  (the SINGLE gate)
├─ dropdown inbox                  All | Alerts | Applications | Activity | System  (§9b/§9c)
├─ "See all" → /notifications      Notification center: 60-day history   — any authed user
├─ ⚙ → /notifications/preferences  per-USER: channels × categories × cadence — any authed user
│    ├─ In-app        [always ready — intrinsic, no connect step]
│    ├─ Telegram DM   [connected ✓]  [type toggles]  [cadence]
│    ├─ Email         [connect →]     [type toggles]  [cadence: daily default]
│    └─ Web push      [enable on this device →]  [type toggles]
└─ "Open Alerts →" → /alerts       the operational BOARD                 — can_alerts_*
     └─ /alerts/group-delivery     per-ACCOUNT: shared destinations × types
          └─ Telegram groups, Sub bots, persona + custom topics
             — can_manage_account, or can_manage_role_bot for one's own row
```

- **Avatar-menu "My Notifications" is REMOVED** — the bell is the only
  door. (The "bell owns its own settings gear" pattern: GitHub/Linear
  inbox. Two entries into one feature was the original confusion.)
- **The redirect runs the OPPOSITE way from the original plan.**
  `/notifications` is a real page (the Notification center) and
  `/notifications/preferences` is the settings page; the historical
  `/alerts/preferences` 301s into preferences so old links and bookmarks
  survive. The alert BOARD stays at `/alerts`.
- SMS has no row on the preferences page: the page iterates REGISTERED
  channels, and the channel is unbuilt.

### 13a. The bell SURFACE — a dropdown inbox, not a hard navigate

Clicking the bell opens a **dropdown notification-center** (GitHub/Linear
pattern) — a quick glance without leaving the page — with "See all" into the
Notification center, "Open Alerts →" into the full board, and the ⚙
preferences. It shipped as
`interfaces/dashboard/src/features/alerts/NotificationsPanel.tsx` (which
cites this section) and then outgrew the alerts-only sketch: it is a
MULTI-SOURCE inbox (§9b/§9c). The sketch below is the shipped shape.

```
        🔔³                                     ← bell + unread count
  ┌─────────────────────────────────────────────┐
  │ Notifications                    ⚙  ↻       │  ← gear → preferences · refresh
  │ [ All 12 ] [ Alerts 9 ] [ Applications 1 ]  │  ← SOURCE tabs (both permission-gated)
  │ [ Activity 2 ] [ System ]                   │
  ├─────────────────────────────────────────────┤
  │ 🔧 Fault SPN 100 — Truck 22        2m    ✓  │  ← icon · vehicle · age · acknowledge
  │ ⛽ Low fuel — Truck 45             18m    ✓  │
  │ 👤 A. Karim joined your team       40m       │  ← inbox notice: click = read + navigate
  │ 📍 Left geofence — Truck 208       1h    ✓  │
  ├─────────────────────────────────────────────┤
  │ Acknowledge all   See all →   Open Alerts → │  ← bulk · centre · the full board
  └─────────────────────────────────────────────┘
```

- **Two tab rows, not one.** The top row picks the SOURCE — All (merged,
  default) · Alerts (permission-gated) · Applications (permission-gated) ·
  Activity · System. The severity filter (All / Critical) is a SUB-row that
  appears only inside the Alerts tab.
- **Each row** = a recent alert or an inbox notice: icon, vehicle/driver or
  actor, age, and — for alerts only — an inline **acknowledge** (act
  without opening the board). Per-row dismiss-✕ was deliberately NOT
  adopted (§9c: a second lifecycle verb on a hover-dependent micro-target).
- **Footer:** "Acknowledge all" (staged) / "Mark all read" (instant) +
  **"See all →"** (the Notification center) + **"Open Alerts →"** (the
  operational board) + the **⚙** (Notification preferences). So the one
  bell reaches *everything*: quick triage (dropdown), history (centre),
  deep triage (board), settings (⚙), from a single door.
- Read/unread state per user lives on the inbox row itself —
  `notification_inbox.read_at` ('' = unread), with a partial index
  `idx_notification_inbox_unread(account_id, user_id) WHERE read_at = ''`
  for the true unread count. There is no `notification_read` table and the
  delivery ledger has no read flag. ALERT rows keep ack semantics instead —
  "unread" there means un-acknowledged — because ack/occurrence semantics
  cannot be honestly mirrored in an inbox row (§9b: never double-store).

**Shipped:** the dropdown is live; the bell is not a navigate-to-/alerts,
and there is no preferences tab on the Alerts board (`AlertsTabs` is
**Board | Group delivery**). The badge = pending alerts + server-true inbox
unread, summed client-side — two stores, two lifecycles, the bell just adds
the numbers.

## 14. Permission model

- **Board** — `can_alerts_*`.
- **Notification preferences** — every authenticated user. The route
  carries no permission wrapper on purpose: the page covers non-alert
  sources too, and each category's `audience` decides what it lists, so a
  user with no alert access still has (non-alert) preferences.
- **Group delivery** — `can_manage_account`, or a role manager for their
  own row (`can_manage_role_bot`).
- Channels honor Vehicle-Access scope: a company/vehicle-restricted user
  only receives alerts for their vehicles (reuse the existing scope
  resolution — delivery must not leak out-of-scope alerts).

## 15. Migration record — the order it actually shipped

*(Step numbers are stable: shipped code comments cite them — "1a", "2a",
"2b", "4a", "4b", step 6. Correct a step's text; never renumber it.)*

0. **Unify the gate — ✅ SHIPPED, then outgrown.** The avatar-menu "My
   Notifications" entry is gone and the bell is the only door. Preferences
   did NOT stay a tab or gear on the Alerts board: they became a top-level
   page, `/notifications/preferences`, and the board header deliberately
   carries none. (§18)
1. **Extract the channel seam — ✅ SHIPPED.** `capabilities/alerting`
   delivery moved behind `Channel`; `telegram_dm` (personal) and
   `telegram_topic` (shared — wraps `resolve_alert_targets` in a
   `DeliveryPlan`) are registered and reproduce the old behaviour. The
   legacy per-subscriber delivery loop was deleted, and a CI layer guard
   now forbids `capabilities/alerting` from importing `telegram` at all.
   *(pure refactor, user-visibly)*
2. **Preference matrix — ✅ SHIPPED** as `migrate_notification_matrix`.
   It creates `notification_pref` (with `cadence`, default `immediate`) +
   `notification_channel` and backfills one `telegram_dm` row per (active
   user × alert type) from the legacy per-user **columns** —
   `alert_faults / alert_health / alert_fuel / alert_geofence /
   alert_events / alert_parking / alert_camera`, seven types, no
   `maintenance` — written as `category = 'alert.<type>'`, plus one
   `notification_channel` row per user from `users.telegram_id` and the
   `alerts_on` master switch. NOT from the `alert_prefs` JSONB.
   `ON CONFLICT DO NOTHING`, so re-runs and later user edits are never
   overwritten. The DELIVERY readers switched; the legacy columns survive
   as a write-mirrored cache (`prefs_mirror.py`), and the column-based
   subscriber reader still used by the non-spine senders
   (`features/events/alert.py`, `interfaces/bot/geofences.py`,
   `capabilities/scorecards/jobs.py` — all `get_typed_alert_subscribers`)
   sits behind `NOTIFICATIONS_MATRIX_READER`, **off by default**: that
   reader flip is the one piece of this step still owed.
   *(the one real migration)*
3. **Digest engine** — ✅ SHIPPED. `notification_digest_queue` +
   `dispatch()` cadence branch (immediate → `channel.send`, batched →
   enqueue) + `flush_digests()` grouping N items into ONE message per
   (recipient, channel), drained by two scheduler jobs
   (`notification_digest_hourly` :05, `notification_digest_daily` 13:00
   UTC). Items clear only after a successful send, so a transport outage
   re-sends instead of losing alerts. Telegram stays immediate, but the
   queue is neither empty nor Email-only: email pref rows inherit the
   channel default cadence `daily` at row-write time, and quiet-hours
   deferrals ride the same table under the internal `quiet` cadence,
   drained by `notification_quiet_flush` (:07) — never by these two jobs.
   Known gap for step 4: the daily flush fires at a fixed UTC hour —
   per-account local morning needs an account filter on the flush, not a
   schema change.
4. **Email channel** — split into 4a/4b.
   - **4a ✅ SHIPPED** — the adapter + the semantic seam. A raw
     `NotificationContent{title, body, alert_type, severity, url, …}` +
     `Channel.render(recipient, content) -> Payload`, escaping exactly
     once at render time. `EmailChannel` renders subject + text/plain +
     text/html + a `List-Unsubscribe` (One-Click) header and sends via
     `capabilities/email.send_email` (in a thread). `dispatch()` +
     `build_digest_content()` render per-channel; digest stores raw
     fields + severity. Telegram render switched to plain `html.escape`
     (no live-link injection from raw content) + a 4096/1024 entity-safe
     clamp. INERT until 4b (no verified address → no email subscriber).
     Design B (semantic content, not enriched Payload) — advisor-settled:
     SMS's short form can't be derived from rendered Telegram HTML.
   - **4b ✅ SHIPPED** — connection lifecycle. Stateless HMAC tokens
     (`tokens.py`, no token table — the signature is the proof), a
     framework-free `lifecycle.py` (connect → verify → send + unsubscribe),
     and a co-located `router.py` (`/notifications/channels/email` connect
     [authed], `/verify` + `/unsubscribe` [public, token-authed]).
     Verify is **address-guarded** (a link for an old address can't verify
     a changed one) and a re-save of an already-verified address is a
     no-op. **GET `/unsubscribe` does NOT mutate** — it renders a confirm
     form that POSTs, so a mail-scanner prefetch can't silently unsubscribe
     a fleet from safety alerts; the RFC 8058 One-Click POST is the machine
     path. Email stays inert until an address is verified. **Default =
     daily digest** is the connect-time cadence, applied at row-write time.
     - **Signing key** — `NOTIFICATION_SIGNING_SECRET` if set, else
       **falls back to `JWT_SECRET`** (the operator default — zero extra
       config; owner-chosen for simplicity after weighing the trade-off).
       Accepted trade-off of the single-secret default: rotating
       `JWT_SECRET` (a session-compromise response) then also invalidates
       every outstanding verify + unsubscribe link, and a `JWT_SECRET`
       leak could forge notification links. A deployment wanting those
       blast radii separated sets the dedicated secret and it quietly
       takes over — no code change. Either way an invalidated link isn't
       catastrophic: tokens self-heal via fresh per-message links, and an
       invalid link hits the graceful "manage preferences" page. Fails
       CLOSED only if BOTH secrets are absent (a misconfig — `JWT_SECRET`
       is required at boot). No kid-ring until a real rotation need exists.
5. **UI consolidation — ✅ SHIPPED, the other way round.** The ADMIN
   routing surface left Settings and became the **Group delivery** tab
   under Alerts (`/alerts/group-delivery`), its routers re-homed under the
   spine at `capabilities/notifications/delivery_admin/`; "Team routing" is
   not the shipped name. PERSONAL preferences left the Alerts area
   entirely: `/notifications/preferences` is their door, `/notifications`
   is the Notification center, and `/alerts/preferences` 301s into
   preferences — the redirect runs the opposite direction from the one
   planned here.
   - **5a ✅ SHIPPED, then superseded** — the bell opens the
     notification-centre DROPDOWN (All/Critical over the alert feed, inline
     acknowledge, empty state; footer = Acknowledge-all + Open-Alerts + the
     ⚙ gear); the feed fetches lazily only while open, badge rides
     useShellStats. UX-audit fixes: "Acknowledge all" (not "Mark read"),
     always-visible row ack (touch). Superseded by the in-app inbox
     (§9b/§9c): the panel is titled **"Notifications"** — alerts are one
     source in it, not the whole panel — it carries five source tabs, and
     alerts' ack-based "unread" now sits beside a real read-state store
     (`notification_inbox.read_at`, POST `/notifications/inbox/read` and
     `/inbox/read-all`).
   - **5b ✅ SHIPPED** — the Email section on the preferences page: connect
     an address (own verification, separate from login email) → confirm →
     pick which alert types go to email + the delivery cadence (defaults
     Daily digest). Endpoints: `GET /notifications/prefs/email`,
     `PUT .../type`, `PUT .../cadence` (role-gated, cadence-validated,
     identity from the authed user). Telegram DM keeps its existing
     `/me/alerts` section (legacy columns) until the matrix reader flip.
   - **5c ✅ SHIPPED, then reversed** — preferences briefly folded into
     `AlertsTabs` as a sibling tab with `/alerts/preferences` as the route;
     two days later they moved to their own top-level door and the redirect
     flipped: `/notifications/preferences` is the route and
     `/alerts/preferences` 301s into it. The bell gear deep-links it, and
     the board header's prefs gear stays dropped. `AlertsTabs` today is
     **Board | Group delivery**. The routing tab was NOT deferred — Forum
     Routing left Settings and ships as **Group delivery**.
6. **Web push ✅ SHIPPED (6-1/6-2/6-3)** — `WebPushChannel` (short-form
   render, fan-out to every device, prunes dead endpoints inline),
   per-DEVICE `push_subscriptions` (browser permission grant = the
   verification; channel un-verifies when the last device goes),
   zero-config VAPID keypair persisted in platform_settings
   (single-writer-wins; private key encrypted at rest), service worker +
   "Enable on this device", and the **"Notify me when" MATRIX grid**
   (types × Telegram/Email/Push) on the preferences page.  Security
   (review + advisor): anti-SSRF endpoint gate (https-only, every
   resolved IP must be globally routable, re-checked at send vs DNS
   rebinding), per-user device cap 10, endpoint REASSIGN-WITH-REPAIR for
   shared cab tablets (dispossessed owner's channel un-verifies in the
   same transaction — no silent blackout; cross-account moves logged at
   warning), webpush timeout+ttl.  **SMS — still open *(design, unbuilt)***:
   OTP + STOP/opt-in + cost limits; just another adapter.  **In-app alert
   banners ✅ SHIPPED** — per-device position pref chosen visually (three
   window mockups, stored per device), cancellable countdown toasts, and a
   baseline-first live-alert differ so opening the dashboard with 40 pending
   alerts fires zero pop-ups.

Data safety: step 2's backfill is additive and idempotent (ON CONFLICT DO
NOTHING) and the legacy `users.alert_*` columns are still written, so it
stayed reversible. One later migration DOES transform data outside step 2:
`migrate_notification_category_rename` renames `alert_type` → `category` on
both `notification_pref` and `notification_digest_queue` and rewrites every
value to `'alert.' || category`. It is guarded on the old column name (a
no-op on a fresh install) and runs FIRST in the notification block, before
the matrix backfill, so the backfill writes the new column name.

## 16. Out of scope (v1) / non-goals

- Per-alert-rule custom thresholds (that's alert *configuration*, a
  different feature).
- Arbitrary user-authored templates (channels own their templates in v1).
- Third-party webhooks as a channel (easy to add later — it's just
  another adapter).
- Collapsing `ai_*` content prefs into channels (§11).

## 17b. Decisions — SETTLED (owner, 2026-07)

1. **Storage → flexible `notification_pref` table.** ✓
2. **Cadence → immediate-only in the first build.** ✓ — and the flag was
   honoured: the hourly + daily digest engine landed before Email was
   turned on for real, and a third, internal cadence (`quiet`) later joined
   the same queue. Telegram (immediate) was unaffected throughout.
3. **Recipients → BOTH personal and shared.** The shared side is ALREADY
   BUILT: `accounts.alert_routing_mode = per_persona_groups`, per-role
   Telegram groups via `persona_mapping.py`, resolved by
   `routing_resolver.resolve_alert_targets()` (+ Owner/Admin aggregate on
   CRITICAL). The new model REUSES this as the first shared channel
   (`telegram_topic`) — the alerting pipeline wraps `resolve_alert_targets`
   into a `DeliveryPlan` executed through that channel; the Forum-Routing
   UI shipped as the **Group delivery** tab, and its admin routers re-homed
   under the spine at `capabilities/notifications/delivery_admin/`.
   No rebuild.
4. **Web push → INCLUDED in v1** ✓ shipped (owner override of the defer rec).
   Sequence it AFTER Email within v1 (Telegram → Email → Web push → SMS):
   push is per-DEVICE (service worker + per-browser subscription + grant),
   so it lands last in v1 to not delay the high-value Email channel.

## 17. Decisions for your review (original — superseded by 17b)

1. **Preference storage — relational `notification_pref` table (rec.) vs
   nested JSONB.** Relational is the extensible shape (shared recipients,
   cadence, delivery-log joins); JSONB is a lighter migration but boxes
   you in. **My rec: relational.**
2. **Digest as a first-class cadence field from day one (rec.) vs
   immediate-only v1.** Your volume makes email unusable without digest.
   **My rec: design the field now, implement immediate + daily first.**
3. **Generalize "shared destinations" now vs keep routing Telegram-topic-
   only initially.** **My rec: model the recipient abstraction now,
   implement only Telegram-topic + personal channels in the first pass.**
4. **Web push per-device complexity — include in v1 or defer.** **My rec:
   defer to a later phase (after Email); it's the fiddliest (per-device
   subscriptions, service worker).**
5. **UI placement — under Alerts now (your ask), with the service kept
   cross-cutting in code.** **My rec: yes.**
6. **Naming — "My Notifications" → "Notification preferences," keep
   "Alerts."** **My rec: yes; cheap, do it first.**
7. **Single gate — the bell owns board + preferences; remove the
   avatar-menu entry. DECIDED (your call).** No second door.

## 18. The gate today — what step "0" delivered, and what outgrew it

Step "0" shipped first, before any channel/matrix work and without touching
data. What survives of it, and what changed:

1. The avatar-menu "My Notifications" entry is **gone** — the topbar bell
   is the sole gate. *(as planned)*
2. Preferences are **not** a tab or ⚙ on the Alerts board: they are a
   top-level page, `/notifications/preferences`, reached from the bell's
   gear, and the Alerts board header deliberately carries no prefs gear.
   *(changed)*
3. The rename landed on the route and the gear, but the shipped page
   **header** reads "Notifications". *(changed)*
4. `/notifications` was **not** redirected into the Alerts area — it became
   the Notification center page (§9c); `/alerts/preferences` redirects into
   preferences instead. *(reversed)*

The two surfaces stayed separate routes, which is exactly why reversing the
redirect cost nothing.
