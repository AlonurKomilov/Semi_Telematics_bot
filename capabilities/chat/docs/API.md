# Chat HTTP API — P2–P4

Implemented locally, 2026-09-16. Both `/api/chat` and `/api/v1/chat` mount the
same adapter. P3 adds durable replay, WebSockets and outbox wakeups; see
[the realtime contract](REALTIME.md). P4 dashboard UI uses these contracts; Notifications remain P5.

Every endpoint requires the normal validated session plus `can_view_chat`.
Account and actor IDs come from that identity; request fields cannot override
them. The normal API billing/quarantine middleware applies. Chat's grant still
defaults off. Responses inherit `Cache-Control: no-store`.

## Conversations and management

| Method/path (relative to `/api/chat`) | Input / result |
|---|---|
| `GET /bootstrap` | Optional `after` conversation UUID and `limit` 1–100 (default 50). Ensures the unique General group, then accessible summaries, latest visible message, caller-private `unread_count`, `unread_mentions`, `has_draft`, caller actions and personal state. Counts exclude own/deleted/hidden messages; draft presence reveals no draft body or another member’s draft. `next_after` continues the scan. |
| `GET /people` | `q` (up to 200 chars), `after` user ID, `limit` 1–100, optional `conversation_id`. Active Chat-granted people, optionally in that conversation. Optional `message_id` with a conversation further restricts edit-mention targets to people who can see that message. Only ID/display name/role; no email, phone or driver PII. |
| `POST /conversations` | `title`, optional `description`, `kind: account\|roles`, `role_keys`, `posting_mode: everyone\|admins`, `new_member_history: all\|since_join`. Caller becomes owner. |
| `POST /direct` | `other_user_id`; returns the existing or newly created same-account pair. |
| `GET /conversations/{cid}` | Metadata and current caller actions. |
| `PATCH /conversations/{cid}` | `expected_version` and `changes` containing only changed title/description/role keys/posting/history/archive fields. Each field needs its current action grant. |
| `GET /conversations/{cid}/members` | Current members, using the people `q`/`after`/`limit` contract. Minimal ID/display name/account role plus `group_role: owner\|admin\|member`. Ordinary eligible members may list; no delegated action details, PII, or access to other DMs. |
| `GET /conversations/{cid}/admins` | Owner-only current delegated rows and group version. |
| `PUT /conversations/{cid}/admins/{uid}` | Owner-only `expected_version`, `actions`. Empty actions still confer admin-only posting. |
| `DELETE /conversations/{cid}/admins/{uid}` | Owner-only `expected_version` query parameter. |
| `POST /conversations/{cid}/transfer-ownership` | `expected_version`, `new_owner_id`; former owner becomes ordinary member. |
| `GET /recovery` | Primary-owner-only eligible recovery records: ID, old owner ID, version. No private names, descriptions, messages or DMs. Paginated like bootstrap. |
| `POST /conversations/{cid}/recover-ownership` | Same payload as transfer, primary-owner-only and only if the old owner is structurally ineligible. Returns a minimal receipt. |

People results scan at most 100 candidates per request. A filtered page may
be short or empty while still having `next_after`; continue until it is null.
Conversation summaries use stable UUID order for pagination; clients may sort
loaded summaries for display. Grant/role/history filtering never happens only
in the browser.

## Messages

```json
{
  "client_message_id": "device-generated-unique-id",
  "body": "Please check this load",
  "reply_to_id": 123,
  "mentions": [456]
}
```

`POST /conversations/{cid}/messages` returns `{message, replayed}` after commit.
Reply and mentions are optional. An optional strict nonnegative `draft_version` consumes the caller’s matching draft in the same transaction as the message and adds the current caller-private `draft` to the response. Its version, normalized body and reply target must match before clearing. This hint is not part of the message idempotency fingerprint. A retry returns the current draft and never clears a newer draft from another device; clients apply drafts monotonically by version. Omitting the hint preserves the original response contract. Generate the client ID once and keep it for
retries. The server normalizes text to NFC and trims message edges; 1–4,000
code points after normalization, nonblank, no NUL. Mentions are at most 50
active, Chat-granted, current audience members, validated by IDs.

An identical normalized body/reply/mention-set under the same conversation,
author and client key returns the saved message's **current** representation.
A changed payload returns 409. A fingerprint preserves this comparison after
edits/tombstones without keeping a content revision archive. A legacy row with
unknown original fingerprint conflicts instead of guessing. Current access
and history boundary apply even to retries. A saved response does not mean
another device received or read the message.

| Method/path under `/conversations/{cid}` | Input / result |
|---|---|
| `GET /messages` | Newest-first history; optional `before` message sequence and `limit` 1–100. Returns `items`, `next_before`. Tombstones remain in history. |
| `GET /messages/{mid}` | One currently visible message. |
| `PATCH /messages/{mid}` | `expected_version`, `body`, optional `mentions` (full replacement, defaults empty). Only the author may edit; reply target stays immutable. |
| `DELETE /messages/{mid}` | `expected_version` query parameter. Own message with current send rights, or delegated delete permission. Clears body/mentions/pins; returns tombstone. |
| `GET /search` | Required literal, case-insensitive `q`, optional `before`/`limit`. Current visibility and nondeleted filtering precede pagination. |
| `GET /pins` | Visible nondeleted shared pins, with the same `before`/`limit` contract. |
| `PUT /pins/{mid}` / `DELETE /pins/{mid}` | Group `pin_messages` action and `expected_version` of the message. Repeating unchanged pin state creates no duplicate event. |

All message representations use the same quote policy. A hidden/deleted reply
target becomes `{"status":"unavailable"}` with no old text or target ID.
Accessible quotes carry ID, author ID and current body. Only the author sees
their `client_message_id`. The request fingerprint is never returned.

## Personal state and drafts

- `GET /state`, `PATCH /state` with changed `muted`, `pinned`, `archived`
  booleans. These belong only to the caller and cannot create membership.
- `PUT /read` with `through_message_seq`. The cursor only advances, cannot
  exceed current message sequence or the server's observed high-water mark,
  and must respect the current history boundary. History/message/search/pin
  reads, returned latest-message summaries and the caller's own sends record
  observation; they **do not** automatically mark messages read. Observation
  is server response preparation, not proof of device delivery.
- `GET /draft`, `PUT /draft` with `body`, optional `reply_to_id`,
  `expected_version`. An absent draft has version 0. First save requires 0;
  every save, including clearing with `body: ""`, increments the version.
  Stale saves return 409, so clearing cannot be undone by an old device's save.
  Draft whitespace is preserved; text is NFC-normalized and capped at 4,000.
  Quoted previews obey current visibility on every read.

Unread counts exclude own/deleted/hidden messages. Archived groups block
sending; personal archive/mute preserves membership/history. A DM peer losing
activity or Chat access disables new sends, while the remaining participant
keeps their permitted history.

## Errors, transaction and limits

Domain errors use `detail.code`: inaccessible/unknown resources 404
`not_found`; denied actions 403 `forbidden`; stale version/identity or changed
retry payload 409; group quota 429; invalid domain input 400. Pydantic request
shape/type/size failures use the API's normal 422. Unauthenticated/revoked
sessions are 401, unavailable identity verification is 503.

Limits use the API's existing limiter, keyed by verified account/user:
reads usually 120/minute, search/edit/delete 60, sends 60, group/admin/pin
changes 30 and ownership changes 15. These are endpoint limits with the
existing limiter backend, not a new global multi-worker rate guarantee.

All writes follow account advisory lock then conversation row lock. Message,
mentions, sequence allocation, durable event and reference-only outbox rows
commit together. Failed mutations roll everything back. Event sequence is
separate from message sequence. P3 publishes reference-only `realtime` wakeups;
HTTP/socket replay reauthorizes current grant, audience and history and fetches
current content/tombstones. External Notifications remain P5.

Migration `213_chat_message_api` adds original-request fingerprint and observed
cursor storage to P1. HTTP integration also exposed a missing prerequisite in
existing session-auth work: `migrate_user_auth_version` is now wired into the
normal platform migration runner. It adds the session version and transactional
identity-change revocation trigger required by the existing auth code. Apply
normal migrations before serving the new code; no production migration was
run during implementation. See the existing
[session cutover runbook](../../../docs/runbooks/security-session-cutover.md).


## P3 sync and replay

| Method/path | Result |
|---|---|
| `GET /sync` | Caller-private inbox `revision` and `poll_after_ms: 2000`; an invalidation token, not an unread count. |
| `GET /conversations/{cid}/events` | Optional `after` (0–signed-bigint), `limit` 1–100 (default 50). Current authorized event projections, scan `cursor`, `has_more`, `resync_required` and caller membership `scope`. Omit `after` for a checkpoint before snapshot reads. |
| `WS /ws` | Exact allowed Origin and dashboard cookie. Subscribe/replay protocol, periodic enforcement and recovery described in [REALTIME.md](REALTIME.md). Writes stay HTTP. |

The two new GET endpoints also perform fresh enabled hold checks and use a
120/minute endpoint budget. Cursor gaps in delivered items can be legitimate
visibility filtering; always continue from the server's scan cursor. A true
journal retention gap requires a fresh snapshot. Migration
`214_chat_realtime` adds atomic candidate fanout, private state/draft signals,
caller inbox revisions and outbox leasing queries without changing grants.
