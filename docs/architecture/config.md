# Page config — the per-feature configuration service

How a feature page's sections become configurable, who may configure
them for whom, and the exact opt-in recipe for the next feature.
Shipped 2026-07-28/29 (commits `81781aa`, `4f6abe0`, then the
permission-driven rework).

## The three tiers

Every configurable page resolves its section list through three tiers,
each only allowed to NARROW or ARRANGE — never to grant:

| Tier | Who sets it | Where it lives | Scope |
|---|---|---|---|
| 1. Shipped layout | code | `features/<x>/layouts.ts` per persona | everyone |
| 2. Team default | matrix-authorized users (below) | `page_layouts` table `(account_id, role, feature) → sections` | one role in one account |
| 3. Personal arrangement | each user | preferences service, `page.<feature>.layout` (synced) | that user |

Resolution (`PageLayoutHost` → `resolvePageLayout`): the team default
replaces the shipped layout as the BASE when one is stored and valid;
the personal arrangement applies on top.

**Option A — a default, not a lock.** Tier 2 sets where teammates
START; it never pins them. A section a user surfaced personally
survives a manager's removal. A per-section `locked` flag is
designed-for but deliberately not built.

**Required sections** (`required: true` in the feature's registry) are
a code-level floor no tier can remove — they're also position anchors
(moves can't cross them). Example: the alerts queue IS the page; the
drill-in drawer is what `?alertId=` deep links open.

## Who may set a team default

Decided by the **Permissions matrix** (`can_manage_role_config`,
"Feature Config" row in the Settings group), enforced server-side per
request:

* `can_manage_account` → any role's default (the owner/admin path);
* `can_manage_role_config` → the caller's **own role only**. Seeded on
  at every manager tier via `MANAGER_GRANTS`; the owner may widen it to
  any tier (a trusted plain employee) or revoke it from a manager row.

The **own-role wall is code, not configuration** — no grant combination
lets a fleet user rearrange the safety team's page. (Deliberately
looser than `can_manage_role_bot`, which stays hard-locked to the
manager tier: bot wiring touches message delivery; page config only
arranges sections and is one click to undo.)

**Standard Admin is an intentional tightening.** The pre-matrix server
code let any admin write any role's default; the UI never offered it to
Standard Admins, so nobody exercised that path. Under the matrix model
a Standard Admin (`admin` without the Full-Admin tier) starts with
neither flag — the owner ticks "Feature Config" on the admin row to
give them own-role reach, or Full Admin's `can_manage_account` covers
any role. Pinned by
`tests/test_page_layouts.py::test_standard_admin_needs_the_matrix_grant`.

Reading is unrestricted: every authed user gets the account's stored
defaults (`GET /page-layouts`) because each person needs their own
role's to render, and rows contain nothing but section ids.

## Validation is split on purpose

* **Backend (`interfaces/api/page_layouts.py`) validates SHAPE only** —
  role/feature allow-lists, unique non-empty ids, length caps. It has
  no section registry and must never grow one.
* **Frontend (`sanitizeRoleLayout`) validates MEANING** — unknown ids
  are dropped; a stored default missing a required section is rejected
  **wholesale** (fall back to the shipped layout) so a stale default
  can never half-break a whole team's page at once.

## Where the code lives (feature-centric contract)

* **Per-feature config = inside the feature.** The section registry —
  ids, labels, `required` flags, lazy components — is the feature's own
  file (`features/alerts/registry.ts`). This is the only place that
  knows what the page contains.
* **Shared engine = `features/_lib/`** — `PageLayoutHost`,
  `PageSectionsGear`, `pageLayoutConfig.ts`, `useRolePageLayouts.ts`.
  One engine, like DataGrid; never copy it into a feature.
* **Backend = the service layer**, parallel to the preferences service:
  router at `interfaces/api/page_layouts.py`, storage mixin at
  `adapters/storage/page_layouts.py`. The single centralized bit is the
  `_ALLOWED_FEATURES` allow-list — kept central deliberately, because
  the shape-only backend has nothing else per-feature to hold.

## Opt-in recipe: making the next feature page configurable

1. Give the page a section registry in ITS OWN folder with honest
   `required` flags and user-facing labels (`features/<x>/registry.ts`).
2. Pass `customizable="<feature>"` to its `PageLayoutHost`.
3. Add `"<feature>"` to `_ALLOWED_FEATURES` in
   `interfaces/api/page_layouts.py`.

That's all — the gear, the team-default block, permissions, storage,
and resolution all apply automatically. The preference key
`page.<feature>.layout` and the `feature` string in the table are
**frozen** once shipped: renaming either orphans stored arrangements.

## Invariants (tested)

* `account_id` comes only from the JWT — a hostile tenant can only
  address their own empty namespace (`tests/test_page_layouts.py`).
* Permission changes in the matrix take effect without redeploy
  (per-account rows + cache invalidation), but the grant travels
  through effective-permission resolution — not the JWT — so no
  re-login is needed for it either.
* An invalid team default degrades to the shipped layout, never to a
  crash and never to a partial apply
  (`pageLayoutConfig.test.ts`).
