# Chat — implementation contract

Status: **P0–P4 implemented locally, 2026-09-16**. Schema, effective-permission
integration, durable HTTP APIs, realtime/recovery and responsive dashboard UI
are implemented and tested. P5 notifications/lifecycle is next. Real staging
UI/API acceptance, interaction budgets and the unmet P3 latency targets remain
P6 release gates. No production migration, grant or active deployment was
performed. The [API reference](API.md), [realtime contract](REALTIME.md) and
validation results describe the current implementation; the
P0 UX preview remains a separate fictional-data prototype.

Homes: `capabilities/chat/` owns conversation behaviour; `adapters/storage/chat*.py` owns SQL; `interfaces/dashboard/src/features/chat/` owns the production client.

Data it owns or will touch: current users/accounts/permissions read-only; Chat-owned conversation/message/admin/membership/read/draft/event tables; source contributions to Notifications and lifecycle. P0 touches no account data.

## 1. Product decisions

- First client: dashboard and responsive mobile web. Include same-account 1:1 direct conversations in v1.
- A Chat-enabled employee may create a group within account creation policy. Creation makes that employee the group owner atomically. There is no global `can_manage_chat` flag.
- Group types: whole account or selected real role keys. `General` is the unique system-created account group. `Announcements` is an ordinary group using admin-only posting.
- The creator must belong to the selected audience. Role groups derive participants from Team Management; their admins cannot change an employee's company role. Manually invited subsets, external users and public invite links are later scope.
- Every group has its own Manage group. The owner assigns admins and their action grants. An employee can own one group, administer a second and be a regular member of a third.
- Group settings decide posting and new-member history. These are Chat records, not Team Management permission columns or user UI preferences.
- Drafts and personal room settings are private to the employee. DM contents are available only to its participants; account ownership does not imply access.

Initial implementation defaults: all Chat-enabled employees may create groups; 100 active user-created groups per account (account-configurable); General and DMs do not consume that limit. Title 1–80 characters, description up to 300, message 1–4,000; count characters after documented Unicode normalization, and reject blank-only text. These are engineering defaults, not new commercial plan entitlements. P1 enforces the fixed 100-group default; Config ownership and account overrides are P5 work.

The create form initially selects **All messages** and **All members can send**, and exposes both settings before creation. Group owners can change them later. Account-wide retention defaults to no automatic expiry; account purge still removes Chat data. Pilot review may adjust these defaults before P6.

## 2. Authority and dependency boundaries

| Fact | Authority | Consumer |
|---|---|---|
| Identity, account, active user, current role/tier | Current users + validated session | Chat actor context |
| Access to Chat | Existing effective permission resolver, including plan/account masks | `can_view_chat` in every transport |
| Membership of a role group | Group role audience + current user role | Shared Chat access policy |
| Owner/admin/action grants | Chat conversation owner + group admin rows | Manage group and message moderation |
| Historical message visibility | Chat membership interval + message sequence | History/search/reply/pins/events/notifications |
| Messages, settings, drafts, read cursor | Chat SQL | Service commands and queries |
| Channel preferences, DND | Notifications / Working Hours | Delivery contribution |
| Vehicle/load/work-order data | Its owning feature | Future reference-card resolver |

`can_view_chat` covers opening Chat and normal self-service participation, including message sending and starting conversations. Read-only group behaviour is a group setting. Removing a user's service grant closes Chat regardless of group ownership.

`service.py` and `access.py` must not import FastAPI or `system/`. Transport auth adapts existing verified identity and account enforcement into a framework-free actor. Any quarantine check stays in the API composition/enforcement layer, avoiding an upward capability → system import. Chat calls Notifications; Notifications accepts source callbacks without importing Chat. No empty feature-contribution registry is required for v1.

Chat is a customer service with its own communication data. It is neither the existing AI assistant conversation store nor `adapters/storage/chats.py` (authorized Telegram chats). The service-channel definition in `docs/SERVICES.md` now distinguishes feature aggregators from communication services.

## 3. Group access matrix

Every allowed cell first requires current account, active user, Chat grant and audience membership. These are action rules, not bypasses.

| Action | Member | Delegated admin | Group owner |
|---|---|---|---|
| Read permitted history/search | Yes | Yes | Yes |
| Send/reply/mention | If everyone may post | Yes in admin-only mode too | Yes |
| Edit/delete own message | If current participation allows it | Same | Same |
| Edit another author's text | No | No | No |
| Edit name/description | No | `manage_info` | Yes |
| Change posting/history | No | `manage_settings` | Yes |
| Change role audience | No | `manage_audience` | Yes |
| Shared pin/unpin | No | `pin_messages` | Yes |
| Delete another message | No | `delete_messages` | Yes |
| Archive/unarchive | No | `archive_group` | Yes, except General |
| Appoint/remove admins or alter their grants | No | No | Yes |
| Transfer ownership | No | No | Yes, to an eligible active member |
| Personal mute/pin/archive or own draft | Yes | Yes | Yes |

An admin record with no delegated actions still identifies an admin for admin-only posting; the form explains this. Default admin grants are edit-info and pin-messages. Owner-only actions cannot be delegated in v1. General cannot be globally archived or removed; personal archive remains possible.

UI consumes server-provided caller actions. PATCH validates every changed field against the **current** actor and settings version. Hiding a control is not enforcement. Updates that would remove the current owner's eligibility are rejected until ownership is transferred; external Team Management role/deactivation changes still take precedence.

### Ownership and recovery

- Unique owner per group; create, transfer and admin updates use a conversation lock/version check.
- Transfer cannot target another account, inactive user or ineligible role. Former owner becomes a member by default; no implicit admin grant is retained. The confirmation names both users and the effect.
- General's initial owner is the account's primary owner. Owner identity alone does not confer Chat read access if the service grant is off.
- If the owner is deactivated or loses audience eligibility, the group remains readable by other members and existing eligible admins retain only their delegated actions. Ownership actions require recovery.
- Recovery is limited to the verified account primary owner, plus a valid Chat grant, for an owner who is currently ineligible. It exposes minimal management metadata and assigns an eligible current member; it does not grant the operator access to group message history. No recovery action exists for DMs. Record old/new owner and actor in Activity Trail.
- Account owner offboarding without a successor follows the existing account ownership process; Chat does not invent a second owner SSOT.

## 4. Membership and history transitions

Modes: `all` and `since_join`. The mode is applied when a **structural membership interval** starts. Its `visible_after_message_seq` is 0 for all-history or the latest committed sequence for since-join. Existing members keep their recorded boundary when the group setting changes. The setting label explicitly says it governs future joins/rejoins.

| Trigger | Membership/history effect |
|---|---|
| Group created | Eligible existing users start an interval; no old group messages exist |
| User enters an allowed role / role added to audience | New interval using the group's current history mode |
| User leaves all allowed roles / removed role from audience | End interval and remove live access |
| Role changes but remains in the same group's audience | Preserve interval and boundary |
| User deactivated/reactivated | End interval / start a new one if structurally eligible |
| Chat grant, plan or account hold temporarily denies access | Deny reads/writes/delivery; do not turn a temporary service gate into a structural leave/rejoin |
| Mute, personal archive, group archive | Preserve interval; group archive blocks sends |
| User leaves and later rejoins structurally | New interval using the then-current mode |

A later all-history join intentionally grants earlier messages, including messages during a prior absence. A since-join rejoin excludes prior messages even if the user once saw them. Already delivered content cannot be recalled from a device.

Membership intervals are a **history projection**, not permission authority. Team/user/audience mutation paths must stamp transitions in ordering consistent with conversation commits; simply recording the first time a person opens Chat is incorrect. P1 must inventory all role/deactivation mutation paths and use reliable change records or transactional hooks plus reconciliation. If a boundary is unknown, deny message history until reconciled; never guess an earlier boundary.

Grant caches currently have a 60-second TTL across workers. P1/P3 must retain this explicit upper bound or improve invalidation, with no longer socket cache. Fresh user/session state still governs role/deactivation. Do not promise instant revocation from current cache behaviour. DB verification failures deny protected content delivery.

Message visibility is used for snippets, unread counts, mention targets, quoted previews, pins, search results, replay and notification rendering. An accessible new message replying to an inaccessible old message returns an unavailable quote, not the old text. Filtered event streams return a server scan cursor so intentional visibility filtering is not mistaken for lost transport events.

## 5. Persistence and concurrency contract

The tables are defined in `adapters/storage/chat_schema.py`. P1 must enforce:

1. Account-scoped composite foreign keys for authors, members, replies, pins and conversation children. Shared Postgres is not a separate database per tenant.
2. Canonical sorted participant pair for DM uniqueness; idempotent General system key. Concurrent creation returns the existing conversation.
3. Per-conversation row lock for message/event sequence allocation and commit ordering. Message sequence and event sequence are distinct.
4. `(account_id, conversation_id, author_id, client_message_id)` uniqueness. Repeating the same key with a different normalized payload returns conflict; retry with identical payload returns the original result.
5. Message mutation and durable event/outbox written atomically. Redis is delivery acceleration, never the source of saved-message truth.
6. Settings and message/draft edits use optimistic versions; stale changes return 409. Cursor updates are monotonic and never claim unseen future messages.
7. Delete is a tombstone; ordinary APIs, search, quotes and pending notices no longer expose old text. Audit stores metadata, not an unrequested message-content revision archive.
8. Personal state and drafts cannot create membership. Unread counts exclude own/deleted/invisible messages. Failed local sends have no server sequence and no recipient visibility until commit.

Message lifetime: pending locally → saved after DB commit, or failed with retry using the same client key. Saved does not mean delivered or read. Multi-device delivery and receipt semantics are later additions, not inferred from a checkmark.

## 6. API contracts for P1–P3

Use the app's existing `/api` prefix. Account and author come only from verified actor context. Unknown and inaccessible conversation/message lookups return a non-enumerating 404; missing session 401; valid user without Chat grant 403; version/idempotency conflict 409; quotas 429 or the existing account quota convention.

| Command/query | Payload / result requirement |
|---|---|
| `GET /chat/bootstrap` | Accessible summaries, unread and caller actions only |
| `GET /chat/people` | Paginated ID, display name, avatar and role; no email/phone/driver PII |
| `POST /chat/conversations` | Title, audience kind/roles, posting/history mode; actor becomes owner |
| `POST /chat/direct` | Other user ID; validate same account/active/Chat eligibility; find-or-create pair |
| `PATCH /chat/conversations/{id}` | Expected version + changed settings only; field-specific action checks |
| `PUT/DELETE /chat/conversations/{id}/admins/{user_id}` | Expected group version + delegated actions; owner-only |
| `POST /chat/conversations/{id}/transfer-ownership` | Expected version + eligible new owner ID; explicit confirmation |
| `POST /chat/conversations/{id}/recover-ownership` | Primary-owner recovery under §3 conditions; auditable, no message content |
| Message/history/reply/edit/delete/search/pins | Shared visibility predicate, versions and bounded cursor pagination |
| Read/personal-state/draft | Caller-owned state with current access; no other-user read state mutation |
| `GET /chat/conversations/{id}/events` | Visibility-filtered events + scan cursor + resync flag |
| `WS /chat/events` | Server events and subscription/heartbeat; durable writes remain HTTP |

Example group result, illustrative wire values:

```json
{
  "id": "conversation-id",
  "kind": "roles",
  "role_keys": ["fleet", "dispatcher"],
  "owner_user_id": 123,
  "version": 1,
  "settings": {"posting_mode": "everyone", "new_member_history": "since_join"},
  "caller": {"group_role": "admin", "actions": ["read", "send", "manage_info", "pin_messages"]}
}
```

Shared identifiers describe domains (`role_keys`, `conversation_id`), never persona-specific schema columns. Actual role keys are data: `dispatcher` is the role key; `dispatch` is a persona/subdomain label, not a second role. No API accepts the client's `caller.actions` as authorization.

## 7. Transport and Notifications spike findings

### Session and account enforcement

Read: [auth.py](../../../interfaces/api/auth.py), [deps.py](../../../interfaces/api/deps.py), [app.py](../../../interfaces/api/app.py).

Current `validate_session_payload` checks durable user/session state and `auth_version`, then replaces role/tier claims from current storage. `_resolve_request_identity` applies token audience restrictions; `resolve_request_identity` caches the chosen identity for a request. The dashboard cookie is `auth_token`, configured for `.4truck.us`, SameSite Lax.

**Executed isolated probe:** installed FastAPI 0.135.1 / Starlette 0.52.1, one HTTP route and one WebSocket route, both completed. A `BaseHTTPMiddleware` marker observed only `['http']`. No production app or DB was imported. This proves HTTP middleware alone does not enforce socket account restrictions.

P3 therefore needs shared transport-neutral identity/account enforcement or an API-layer WebSocket adapter around the existing policies. Require the authenticated dashboard cookie, known Origin allowlist, accepted token audience, active account/user, current grant and membership before accept/subscription and protected delivery. Re-check expiry/holds while connected. No durable token in the URL, no permissive missing-Origin browser flow, no capabilities → system import. The production cookie handshake itself has not been tested in P0.

### Proxy and workers

Read: [gunicorn.conf.py](../../../gunicorn.conf.py), [nginx/4truck.conf](../../../nginx/4truck.conf), [legacy Nginx config](../../../nginx/semi-telematics-bot.conf), [Vite config](../../../interfaces/dashboard/vite.config.js).

Gunicorn uses multiple Uvicorn workers. Checked-in dashboard `/api/` proxy locations set `Connection ""`, not WebSocket upgrade forwarding; the Vite `/api` proxy is a string without explicit `ws: true`. P3 must provide a narrow Chat socket location/upgrade configuration and verify it in the actual serving vhosts. Checked-in files are evidence of a required review, not proof of the deployed server configuration. No deployment config was changed or remote host inspected in P0.

Reconnect subscribes before DB catch-up, buffers live events, deduplicates by event ID/version and recovers gaps from the durable journal. A retained-cursor miss triggers resync. Redis outages fall back to bounded HTTP reconciliation. Outbox uses leases/retry; external delivery is not promised exactly once.

### Notification seam

Read: [categories.py](../../notifications/categories.py), [service.py](../../notifications/service.py), [router.py](../../notifications/router.py).

`NotificationCategory` supports category, kind, optional role audience and permission requirement, but no message-resource authorization callback. `notify_user` is targeted and immediate; digest and quiet flush paths use their existing channel/recipient checks and cached summaries. They cannot decide whether a former Chat member may still see a queued message.

P5 adds a generic source-authorizer contract registered by Chat. Input: account, recipient, source and resource/event references. Output: allow with freshly rendered safe content, drop because access/content is gone, or retry because verification is temporarily unavailable. Missing authorizer for a protected Chat source denies delivery; exceptions cannot fall back to stale text. Batch filtering applies per item **before** a digest renderer, and the same hook applies to inbox reads and unread counts. Existing categories retain their behaviour with regression coverage.

MVP Chat notices use in-app channel only, targeted DM/mention/reply, author excluded and one notice per recipient/message. Group mute and Notifications/DND preferences both apply; Notifications off does not disable Chat. External channels and their deferred paths must pass this hook before being enabled. No Chat category or delivery is registered in P0.

## 8. Validation fixture and P1 handoff

The staging load for the capacity run is defined as: 2 API workers, 5 accounts, 100 connections, 5 messages/second and 100,000 stored messages. Proposed healthy-staging targets: durable send p95 <500 ms, live update p95 <1 second. These have **not been measured**. P3/P6 will execute them with documented server resources, hot-room contention, reconnect and Redis outage cases.

P0 preview validation is recorded in working notes kept outside the repository. Client-side role simulation is UX evidence only; it is not a tenant-isolation, session-security or database test.

P1 is implemented below. Pilot grants remain a P6 release action; P1 only
adds the default-off field and the existing matrix edit path.

## 9. P1 implementation and validation

### Code and authority

- `adapters/storage/chat_schema.py`, registered as `212_chat_foundation` in
  `migrations.py`: 12 additive tables, composite account FKs, canonical DM
  pair, unique General, immutable conversation identity, reference-only
  event journal and delivery schema. The native migration is atomic and
  repeatable; reruns preserve data. No stored plan/role grant is rewritten.
- `adapters/storage/chat.py`: tenant-scoped persistence mixed into `Database`
  as `ChatMixin`, distinct from the Telegram `ChatsMixin`. Writes use the
  caller's transaction and pinned connection.
- `capabilities/chat/access.py`, `models.py`, `service.py`: framework-neutral
  access plus General/DM find-or-create, group create/settings, granular
  delegation, optimistic versions, archive, transfer and recovery. They
  were not mounted at the P1 checkpoint. P2 now mounts `router.py` with actor
  IDs from validated API identity and billing/session/quarantine at transport.
- `can_view_chat` is registered in FeatureSet/taxonomy/registry, dashboard
  catalog/types and staff/driver permission rows. It defaults off, even for
  owner and senior tiers. Plan inclusion applies normally; no new global
  manage flag. P1 reserved `/chat` with `navHidden: true`; P4 registers the real page and navigation.
- Group metadata mutations write Activity Trail and a reference-only sync
  event in the same transaction. Private names/descriptions and DM creation
  are not copied into account audit events. Notifications/outbox dispatch,
  audit presentation/registry integration and account lifecycle follow the
  later phases (outbox dispatch in P3, Notifications/lifecycle in P5).

### Structural membership and locking

Inventoried user paths: `create_user`, `create_pending_user`,
`create_user_with_email`, `update_user`, `remove_user`, and direct user SQL
writers. INSERT and actual `role`/`is_active` changes are captured by DB
triggers in the writer's transaction, including rollback. Audience inserts,
updates and deletes are captured too. Retained roles are not deleted and
reinserted when an audience changes, preserving their existing intervals.

All Chat writers take `chat_lock_account(account_id)` before locking a
conversation row. User membership triggers use that same advisory lock;
conversation locks and committed `message_seq` establish the join boundary.
This deliberately serializes account Chat writes in v1. P3 must benchmark
hot accounts before reducing lock scope. Future message writers must use
this ordering; plain SQL writers must not bypass the storage contract.

An active structural member gets an interval even while their Chat grant is
off. Permission/plan/account holds deny access without changing that interval.
Settings changes affect future joins only. Missing interval denies access;
there is no lazy first-open backdating or speculative history reconciliation.
Repairing corrupted history requires trustworthy transition evidence.

Moving a user row with Chat references to another account is rejected by
composite FKs. Normal Team Management has no account-move operation; offboard
and create a new identity instead of moving historical communication across
tenants. Purge dependency ordering remains P5 work.

The permission resolver uses a separate read connection, so grants resolve
before entering the Chat transaction. Under the account lock, the service
rechecks live identity/active state and role/tier; changed identity returns
`identity_changed` for a retry, never an old role's grant. There is no extra
Chat permission cache. The existing cross-worker grant TTL remains 60s.
DM send actions additionally require a currently Chat-granted active peer;
peer grant loss leaves the remaining participant's authorized history intact.

### Executed validation (isolated PostgreSQL 16, no production DB)

- **100 backend tests passed**, including 42 new Chat domain/storage cases
  and existing plan, service grant, registry drift, permission surface,
  layer boundary and test layout guards.
- Tested concurrent General/DM creation with a cold grant cache and pool size
  two; version conflicts; tenant/DM/role isolation; foreign-account authors,
  admins, intervals, drafts, states, mentions, pins and deliveries rejected
  by the DB; cross-conversation replies/pins/events rejected.
- Tested role transitions that preserve membership, leave/rejoin, activation,
  history mode sampling, new-user joins, membership waiting for a message
  commit, raw SQL and normal user-mixin mutations, transaction rollback,
  missing projection denial, audit failure rollback, migration reruns.
- Tested owner/admin boundaries, field-specific edits, empty-grant admins,
  archive protection, quota/unarchive, transfer, primary-owner-only recovery,
  temporary permission loss, effective tier/plan masks, role-change races,
  Unicode normalization and private metadata omission from audit.
- TypeScript `tsc --noEmit` passed; changed-file ESLint has zero errors and
  one pre-existing unused-disable warning in `types/index.ts`.
- Frontend matrix/icon run: **39 passed, one existing ELD config-grid failure**
  (`verbGrid.test.ts:65` expects a list missing `can_view_eld`). Reproduced
  the identical failure from clean `HEAD` files in a temporary directory.
  Chat's service/catalog completeness checks passed. No unrelated fix applied.
- Locale parity and generated navigation: **22 additional tests passed**
  (frontend total: 61 passed, one independently confirmed existing failure).

The P1 handoff is implemented in P2 below: canonical original-request
fingerprints, authenticated APIs, shared visibility and atomic message/event/
outbox persistence. Actual delivery remains P3/P5 work.

## 10. P2 implementation and validation

### Implementation

- `router.py`: 26 HTTP operations mounted at `/api/chat` and `/api/v1/chat`,
  each behind `require_permission('can_view_chat')` through its dependency
  tree. The ordinary session resolver, billing/quarantine middleware and
  no-store responses apply. Actor/account/actions cannot come from request
  JSON. Requests have bounded schemas/cursors and per-member endpoint limits.
- `messages.py`: sends with original-payload fingerprint and durable retry,
  descending history, reply previews, author-only edits, authorized tombstones,
  literal search, shared pins, private drafts/state and monotonic read cursors.
  `validation.py` owns NFC normalization and domain limits across transports.
- `queries.py`: paginated summaries/unread, privacy-minimal people picker,
  owner admin list and primary-owner recovery metadata. No email/phone/driver
  fields or other employees' drafts are returned. Edit-mention targets must
  be able to see the existing message, not merely belong to its room now.
- `adapters/storage/chat_messages.py` and `chat_queries.py`: all new SQL.
  Message, mention changes, event, candidate outbox fanout and both sequence
  counters share the Chat transaction/lock ordering. Event/delivery rows keep
  references, never copies of message body. A failed outbox write rolls back
  the message and sequence allocation too.
- Migration `213_chat_message_api`: original request fingerprint and the
  server-observed message cursor, plus message/mention indexes. Existing
  fingerprints are not invented from potentially edited/deleted content.
  Tombstones erase text/mentions/pins and old quotes become unavailable;
  retries cannot recreate deleted content.
- History boundary filtering precedes content pagination and applies to
  message reads, search, summaries, unread, pins, reply/draft previews,
  retry responses and mention eligibility. Settings still affect future
  structural joins only.

### Auth prerequisite found during integration

The first real signed request failed with 503 / `UndefinedColumnError:
u.auth_version`. The working tree's existing session validator, identity
writers, runbook and auth regression tests already required
`migrate_user_auth_version`, but the migration was absent. P2 supplies that
additive platform migration and wires it into normal startup: durable version,
transactional increments/revocation for identity/credential changes, no
invalidation for ordinary profile edits. Auth reader semantics were preserved.
The auth and extension regression suites are included in validation.

### Validation

- **174 regression tests passed** (`-n 2`): all Chat domain/HTTP tests,
  Chat schema, auth-version/session and enforcement tests, extension-token
  regression, every-route gate, layer boundary, test layout, process-global
  isolation, permission surface and registry drift guards.
- **6 final targeted tests passed** after the last edit-mention visibility
  change: target history boundaries, inaccessible quotes/search/pins/retries,
  people filtering, real HTTP message CRUD/state flow and recovery metadata.
  This run includes two newly added cases and four regression rechecks.
- HTTP tests exercise both API prefixes with real signed tokens and cookie
  fallback, 401/403/404/409/422 behavior, spoofed identity rejection, account/DM
  isolation, billing/quarantine holds, delegated management, optimistic edits,
  and a per-member send budget (one exhausted member does not block another).
- Storage/domain tests prove concurrent retry dedupe, committed message/event
  ordering, original-payload fingerprints after edit/delete, full outbox
  rollback, draft-clear version continuity, observed monotonic read cursors,
  migration reruns and tombstone removal from all content projections.
- Syntax compilation and changed-file whitespace checks passed. Only existing
  `datetime.utcnow()` migration deprecation warnings were emitted.

All DB work uses isolated Testcontainers PostgreSQL; no production DB,
permission grant, external message, restart or deployment was involved.
P2 has no frontend implementation change.

### P3 handoff recorded at the P2 checkpoint

Use the [API contract](API.md) as the HTTP fallback and durable write surface.
The `realtime` outbox contains candidate recipient references, including the
sender for other devices; candidates are **not** authorization decisions.
Before delivery, recheck session/account hold, current effective Chat grant,
structural membership, interval boundary and current message/tombstone.

Implement multi-worker pub/sub, outbox leases/retry, WebSocket handshake and
periodic enforcement, visibility-filtered event replay with scan cursors and
resync, and proxy upgrades. Group metadata events are already durable; P3
must add their fanout and membership/ACL invalidation signaling. Personal
state/draft synchronization between devices also belongs in that wiring.
A worker must never publish all Chat journal rows without resource checks.

Benchmark the account lock and literal search on the capacity fixture before
claiming latency targets. Run real two-worker reconnect/restart/Redis-outage
probes. Notifications, retention/purge and Config policy ownership remain P5;
attachments remain P7. The production UI is still P4.


## 11. P3 implementation and P4 handoff

P3 implements [the realtime contract](REALTIME.md): two HTTP recovery endpoints,
WebSocket replay with current session/hold/resource checks, a reference-only
Redis/Sentinel wakeup channel, leased outbox processing per API worker, bounded
connection/input/output buffering, private draft/read/state invalidations and
durable caller inbox revisions. Migration `214_chat_realtime` is additive and
repeatable; no grant defaults changed. Redis outages and worker death recover
from journal scan cursors. Old events render current resources/tombstones.

Group management and membership invalidations use the same inbox revision as
message activity, including rooms not currently subscribed. Neither an outbox
candidate nor a Redis hint grants access. Protected payloads are resolved at
the receiving HTTP/socket boundary. Session/quarantine/billing enforcement lives
in `interfaces/api/chat_security.py`; the customer capability does not import
operator services. Socket lifecycle is owned by the API lifespan, and checked-in
nginx/Vite/Gunicorn/legacy-runner settings support the bounded transport.

Validation and measured local capacity are recorded in working notes kept outside the repository.
The 100-connection fixture delivered all messages but missed both latency
targets (send/live p95 3.91/6.66 s); performance remains an explicit P6 release gate.
No production restart, nginx reload, migration execution or pilot grant was
performed by this work. Real TLS/Cloudflare/staging smoke remains P6.

The P3 checkpoint handed the HTTP and realtime contracts to P4; the implemented
client is described below. The handoff requirements were: Implement checkpoint-before-snapshot, cursor persistence
after applying batches, event/message-version dedupe, membership-scope cache
reset, inbox invalidation, bounded fallback polling and reconnect backoff.
Permission/persona preview must not manufacture a Chat actor. Manage group
must use server-returned current actions and existing optimistic versions.
Notifications/retention/purge/exported metrics remain P5; media remains P7.


## 12. P4 dashboard implementation and P5 handoff

The production page lives in `interfaces/dashboard/src/features/chat/` and is
lazy-loaded at `/chat`, with catalog/sidebar, topbar launcher and command-palette registration.
The sidebar and topbar resolve the catalog grant through `useViewPermissions` /
`RoleViewContext`, also used by `ProtectedRoute`. Permissions preview carries the
exact server row (`owner`, `owner__co`, or a role's base/manager row), including
same-role previews and cross-subdomain navigation. Missing or failed preview
rows produce a retry state, never synthetic grants or the viewer's permissions.
“My dashboard” exits preview and restores the real `/user/me` permissions.
The selector shows the actual or previewed tier; Permissions initially selects
and marks the signed-in member's own tier with “You”. Preview rows refresh on
window focus and after a successful permissions save.

The page requires AuthContext's real user/account; it does not add a second
raw-permission redirect that conflicts with the shared view guard. Every backend
request resolves that real member through Team Management identity and the
shared effective-permission resolver. If the preview permits Chat but the real
member is denied, Chat stays on its route with an identity explanation and a
retry action that refreshes `/user/me` and starts a fresh session. Terminal
authorization failure clears private state and hides creation controls. A
single unavailable room does not become a global Chat denial. Persona selection
never supplies an actor or grants content access. Group and message controls
follow current server actions; account owner is not implicitly every group's
owner.

`api.ts` and `types.ts` own the wire contract. `session.ts` owns account/user-scoped
in-memory snapshots, checkpoint-before-snapshot/replay, cursor advancement only
after dependent reads, current-version/tombstone merging, interval-boundary
reset, bounded live buffering, visible-tab fallback, and reconnect backoff.
Abort/generation/epoch checks prevent late requests from restoring content from
a previous room or actor. Scope reset unmounts search, reply, draft and management
state; access denial clears the protected thread and inbox. No chat content,
retry payload or cursor is written to localStorage or a shared query cache.

`Chat.tsx` renders a two-pane desktop inbox and one-pane mobile navigation,
private unread/mention/draft indicators, filters, personal state and group/DM
creation. `Thread.tsx` has a 100-message window with previous-page anchor
preservation, latest/message deep links, and focused-visible-bottom-only read
updates. Message rows are memoized independently of draft/read/inbox refreshes;
permission flags and current message objects still invalidate their actions.
Composer state stays local during typing. `draft.ts` serializes versioned saves;
conflicts preserve local text until the user explicitly loads the remote draft.
A pending retry keeps the original message key and normalized payload.

`GroupDialog.tsx` uses optimistic versions for title, audience, posting/history,
delegated administrator actions, transfer and archive. Read-only members have a
minimal roster; delegated administrators cannot grant themselves owner rights.
`contextMenu.tsx` declares shared menu actions. Dialog, Card, Button, scrolling,
icons, semantic colors, user timezone and all nine locales use existing shared
contracts. Search and pins fetch current authorized projections and invalidate
on live changes. Mention selection never infers identities from display names.

P4 adds no migration. Bootstrap now projects caller-private unread mention count
and draft presence in the existing aggregate query. The paginated `/members`
endpoint reuses the authorized people projection with minimal group-role labels.
Optional `draft_version` on send clears only the matching caller draft inside
the message transaction; a lost response/reload cannot restore already-sent text,
and an old idempotent retry cannot erase a newer draft. SQL remains in storage
adapters; Team Management and permission/plan resolution remain their own SSOTs.

Known v1 boundary: draft text and reply sync, but mention IDs are not part of the
draft schema. The picker explains that mentions must be selected again after a
draft reload. The pending retry envelope lives only in the current tab; the
atomic send/draft transaction protects the already-committed reload case.
Primary-owner emergency recovery remains the minimal API workflow from P2;
normal group ownership transfer is available in the UI. Attachments, typing,
presence, external delivery and read-receipt lists are not P4 functionality.

P5 is next: register Chat notification categories and a generic, fail-closed
source authorization hook in Notifications, dedupe mention/reply/DM recipients,
check current membership and mute preferences at actual delivery, then implement
config ownership, retention/account purge, audit and operational metrics.
Do not copy message bodies to the notice queue or make Chat depend on a new
employee/role registry. Notifications being disabled must not break Chat.
P6 still owns real UI/API two-browser acceptance, TLS/proxy/pilot operations,
mobile keyboard/screen-reader checks and the unmet P3 capacity targets.
