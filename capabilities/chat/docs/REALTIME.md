# Chat realtime — P3 transport contract

The database is authoritative. HTTP commits writes; WebSockets deliver visibility-filtered journal batches. Redis only transports `[account_id, user_id]` wakeups. An outbox acknowledgment means a hint was published, not that a device received or read a message. Delivery can repeat; clients must deduplicate.

## Connect and authorize

`/api/chat/ws` and `/api/v1/chat/ws` accept the dashboard `auth_token` cookie and an exact allowed browser Origin. Missing/unknown Origin, query parameters, Authorization headers, subprotocol credentials, scoped tokens, extension/setup/unknown audiences and rowless operator sessions are refused. The Origin set is shared with the API CORS allowlist; `*` is excluded. Local Vite origins must be explicitly configured through `CORS_ALLOWED_ORIGINS`.

Before accepting, on every reconciliation pass, and before each conversation batch, the adapter verifies JWT expiry/signature, durable user/session state, active account, effective Chat grant and enabled quarantine/billing policy. Quarantine checks read current person/account standing directly. Verification failures close the stream; HTTP middleware alone cannot protect WebSockets. The existing grant resolver still has its **60-second cross-worker cache bound**; the socket introduces no additional grant cache. Fresh role/account/session changes are checked on each pass. Resource access and membership boundaries are rechecked under the Chat transaction lock.

Limits are **per API worker**: 1,000 connections, five per user, 20 subscribed conversations per connection, ten subscription commands per ten seconds. Socket commands are at most 4,096 bytes; Gunicorn and legacy `run.py` bound Uvicorn's input queue to four frames. Outgoing batches scan at most 50 events. Slow writes have a five-second deadline and close with 1013. No message-content queue accumulates in memory; wakeups coalesce into one bit per connection. Normal clients reconnect with exponential backoff and jitter, and use HTTP while the socket is unavailable.

Close codes: 1008 invalid authentication/policy/protocol; 1013 connection/command/write budget or unavailable runtime; 1011 verification/storage failure. A pre-accept close appears to browser clients as a refused handshake.

## Wire protocol

Server first sends:

```json
{"type":"ready","protocol":1,"poll_after_ms":2000,"max_subscriptions":20}
```

Client replaces its complete subscription set with:

```json
{"type":"subscribe","conversations":[{"id":"conversation-uuid","after":42}]}
```

Omitting `after` obtains a current checkpoint and asks for a snapshot. An empty conversation list retains authenticated inbox invalidations. There are no socket writes, typing, presence or delivery acknowledgments in P3.

Server frames:

- `sync`: `{type, revision}`. A caller-private, monotonic inbox invalidation token. Refresh/debounce HTTP bootstrap after a change, including new rooms and messages in unsubscribed rooms. It contains no conversation IDs or content and is not an unread count. Membership changes also advance it.
- `events`: `{type, conversation_id, items, cursor, has_more, resync_required, scope, caller}`. `caller` carries current group role/actions, and is re-emitted on changes even without a journal event (for example, a DM peer's Chat grant is revoked). `scope` contains this user's current `membership_id` and `visible_after_message_seq`. Each visible item contains `id`, `event_seq`, `kind`, `resource_version`, and, for message events, a freshly authorized current `message` representation.
- `removed`: `{type, conversation_id}`. Discard that room's local content and subscription; access has disappeared. A caller-supplied unknown ID receives the same response.
- `heartbeat`: sent at least every 20 seconds while healthy.

`cursor` is a **scanned journal sequence**, not the final delivered event. Private events for other people and hidden old-message events are omitted while the scan cursor advances. Never infer missing events from gaps between delivered items. `has_more` means continue from the returned cursor. A journal hole, a cursor beyond the head, or a requested initial checkpoint produces `resync_required=true` and the current cursor, with no event content.

Message events render the **current** resource, not an old body from the journal. After create→edit→delete, replay of all three references returns the current tombstone. Deduplicate event IDs and apply message snapshots by **`message.version`**, not the older event's resource version. A pin/unpin event also invalidates the pins endpoint. Group events invalidate conversation metadata/actions/admins as applicable. `state_changed` and `draft_changed` invalidate only the caller's own HTTP resource; their journal rows contain no private body.

## Snapshot and reconnect order — implemented in P4

1. Establish the socket and subscribe **before** catch-up, with the last successfully applied cursor (or no `after` for a first open).
2. With a new checkpoint/resync, buffer subsequent batches, fetch HTTP conversation/history/pins/state/draft snapshots, then replay again from that checkpoint and merge buffered batches. Taking the checkpoint before the snapshots prevents a write in between from being lost.
3. On membership interval/boundary changes, discard invalid local content and rebuild the snapshot; always remove messages at/below the new boundary. Do not preserve cached search results, reply quotes or drafts from a previous scope without re-fetching them.
4. Persist a cursor only after applying that batch. Deduplicate replay/live overlap, and re-fetch permissions/actions on `sync` or relevant metadata events. Socket removal/closure must stop presenting stale content as current authorized data.
5. Reconnect with the saved cursors. The server subscribes its local wakeup before replay, retains signals arriving during catch-up and scans the DB on a two-second idle timer even if Redis loses a hint. Duplicate hints whose durable caller revision has not changed do not rerun the permission pipeline; they do not postpone the periodic check and never emit protected content. A fresh command, changed revision or due timer always reauthorizes. A journal retention gap requires the snapshot sequence above.

HTTP fallback uses the same filtering:

- `GET /api/chat/sync` → `{revision,poll_after_ms:2000}`.
- `GET /api/chat/conversations/{id}/events?after=42&limit=50` → the event envelope without `type`. Omit `after` for a checkpoint. `limit` is 1–100.

Both aliases support these endpoints. They require `can_view_chat`, enabled hold checks, and the normal current session; responses are `no-store`. Each endpoint family has a 120/minute per-user budget in the existing limiter. A fallback client polls the visible room and inbox on a two-second interval with jitter, drains `has_more` within budget, pauses background tabs, and reduces frequency for additional rooms. It must not poll all 20 socket subscriptions every two seconds over HTTP. All durable writes continue through existing HTTP endpoints.

## Worker and storage lifecycle

Migration `214_chat_realtime` adds the event recipient FK, caller sync revision table, atomic event fanout and private-state/membership triggers. It is additive and repeatable. No Chat grant changes. Group/message/private events, candidates and revisions commit with their source write. A body is never copied into the outbox, Redis or audit metadata.

Each API lifespan owns one outbox task and one Redis subscription task. They stop before the platform DB/Redis close. The shared Redis client retains its existing direct/Sentinel configuration. Outbox claims use `FOR UPDATE SKIP LOCKED`, 30-second leases and unique lease tokens. Stale workers cannot acknowledge a replacement lease. Failures release a row for exponential retry (2–60 seconds); each batch uses one Redis pipeline and one fenced DB acknowledgment; a broker failure defers the batch. Partial publication may safely repeat. Worker death leaves leases for recovery. Repeated publication is harmless. Publication to zero subscribers is still acknowledged: offline recovery uses the journal, not the delivery rows.

Outbox rows are candidate hints, never access grants. Protected content is only resolved by HTTP or an authenticated socket at delivery. An old candidate may publish an opaque user wakeup after membership/grant removal; the resolver will disclose no forbidden resource. Membership invalidation is durable in the caller's revision and detected by periodic reconciliation even without a broker.

P3 keeps the P1 account-before-conversation lock ordering for mutations and protected conversation reads. Reading an opaque caller-only revision resolves the actor grant but does not take a conversation/account write lock. Load results belong in the validation notes kept outside the repository; lock partitioning and substring-search indexing should follow evidence. Retention, purge, product configuration and exported operational metrics remain P5. Runtime counters (`published`, `retries`, `subscriber_reconnects`) are instance-owned; logs do not include tokens, bodies or per-person identifiers.

## Operational rollout boundary

The checked-in nginx configurations upgrade only Chat socket paths; Vite enables WebSocket proxying for `/api`. The API subdomain's root paths rewrite to `/api` as before. The new Gunicorn worker retains Uvicorn's normal HTTP behavior while bounding socket input. No active nginx reload, API restart, production migration execution or pilot grant is part of this implementation.

Before P6 rollout: apply additive migrations through the normal deployment sequence, verify exact public Origins/cookie policy, use the configured worker class, validate real TLS/Cloudflare/proxy idle behavior, and run the two-browser smoke on different workers. The local proxy syntax test uses temporary high-port copies without deployment SSL/Cloudflare prerequisites; it does not certify the live edge.

Rollback disables Chat routes/entry and stops its runtime with the worker deployment, preserving Chat tables and journal. A rolling restart is recoverable by saved cursors. Keep HTTP fallback available if only the WebSocket edge is disabled. P4 implements and tests the client recovery contract in `interfaces/dashboard/src/features/chat/session.ts`. Its cursor is retained only in the current authenticated session’s memory, after a batch and its dependent resources have applied; reload takes a fresh checkpoint. Message content and cursors are not persisted in browser storage. One visible room is subscribed; hidden tabs pause, live frames are bounded to eight queued batches, message rendering to 100 rows, and inbox rendering to 50 rows per page. Staging two-browser and edge acceptance remain P6 release gates.
