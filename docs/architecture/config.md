# The config family — per-feature configuration, one system

SSOT for how features expose configuration, who may change it for whom,
and the opt-in recipes. Shipped 2026-07-28/29 (page layouts:
`81781aa` → `4f6abe0` → permission-driven rework; family unification:
this document's commit).

## One family, two scopes

Every piece of feature configuration in the platform has exactly one of
two scopes, and each scope has exactly one store and one permission —
**no new config ever adds a table or a permission flag**:

| Scope | What it holds | Store | Permission | Reach |
|---|---|---|---|---|
| **Role** | how a feature page is *arranged* for one role | `page_layouts` table `(account_id, role, feature) → sections` | `can_manage_config_role` | caller's OWN role only; `can_manage_account` crosses roles |
| **Account** | a feature's *shared settings* — one truth for everyone | `account_settings` key-value rows (feature-owned keys) | `can_manage_config_all` | account-wide |

Per-USER view state is deliberately outside the family — that's the
preferences service (`page.<feature>.layout`, synced), resolved on top
of the role tier (Option A below).

## The blast-radius rule (why scope is not a choice)

**Scope follows blast radius.** Config that changes how a page *looks*
may vary by role. Config that changes what data *means* — thresholds,
scoring rules, anything a computation reads — is account-wide, always.
Two roles must never disagree about whether the same driver grades
"good"; the number stops meaning anything the moment they can.

This is why the naming pair is asymmetric on purpose:
`can_manage_config_role` names the scope (the role's own arrangement);
`can_manage_config_all` names the reach (the feature's settings, shared
by all). A feature's settings cannot be role-scoped, so no third
combination exists.

## Who may change what

Decided by the **Permissions matrix** (two adjacent rows in the
Settings group), enforced server-side per request through
effective-permission resolution (tier row + per-account overrides —
matrix edits apply without redeploy or re-login):

* **Config — own role** (`can_manage_config_role`): seeded on at every
  manager tier via `MANAGER_GRANTS`; owner may widen to any tier (a
  trusted plain employee) or revoke from a manager row. The own-role
  wall is code: no grant combination lets a fleet user rearrange the
  safety team's page. Only `can_manage_account` crosses roles.
* **Config — account-wide** (`can_manage_config_all`): seeded on for
  the OWNER and the SAFETY manager tier (which historically owned the
  scoring config); everyone else gets it only when the owner ticks it.
  KPI thresholds additionally accept `can_manage_account`, preserving
  Full Admin's historical reach there.
* `can_manage_role_bot` deliberately stays OUTSIDE the family,
  hard-locked to the manager tier — bot wiring touches message
  delivery, a different risk class than config.

### History (keys that fed the family)

* `can_manage_role_pages` → `can_manage_role_config` →
  **`can_manage_config_role`** (renamed twice in its first two days,
  never matrix-visible under the old spellings).
* `can_manage_scorecard_rules` → folded into
  **`can_manage_config_all`** (2026-07-29). Stored per-account grants
  carried over by migration (`migrate_config_perm_keys`, OR-merge);
  the Safety-manager seed carried into `TIER_GRANTS`; the standalone
  matrix row was removed. Consequence, chosen knowingly: an owner can
  no longer grant scorecard-rules editing *without* KPI-thresholds
  editing — the family trades that granularity for never growing
  another flag.

## Role scope: team-default page layouts

A role manager arranges a feature page and saves it as the team's
starting point; every teammate's page then starts from it.

* **Three tiers**, resolved in `PageLayoutHost` → `resolvePageLayout`:
  shipped per-persona layout → stored team default (replaces the base
  when valid) → the user's personal arrangement on top.
* **Option A — a default, not a lock.** A section a user surfaced
  personally survives a manager's removal. A per-section `locked` flag
  is designed-for but not built.
* **Required sections** (`required: true` in the feature registry) are
  a code-level floor no tier can remove, and position anchors moves
  can't cross.
* **Validation is split**: the backend
  (`interfaces/api/page_layouts.py`) checks SHAPE only (allow-lists,
  unique non-empty ids) and must never grow a section registry; the
  frontend (`sanitizeRoleLayout`) checks MEANING and rejects an invalid
  stored default **wholesale** so a stale default can't half-break a
  whole team at once.
* Reading is unrestricted (`GET /page-layouts`): everyone needs their
  own role's default to render, and rows hold nothing but section ids.
* **Standard Admin** starts with neither flag (intentional tightening —
  the pre-matrix any-role bypass was never reachable from the UI);
  the owner delegates via the matrix. Pinned by
  `tests/test_page_layouts.py::test_standard_admin_needs_the_matrix_grant`.

### Opt-in recipe (next configurable page)

1. Section registry in the feature's OWN folder with honest `required`
   flags and user-facing labels (`features/<x>/registry.ts`).
2. `customizable="<feature>"` on its `PageLayoutHost`.
3. Add `"<feature>"` to `_ALLOWED_FEATURES` in
   `interfaces/api/page_layouts.py`.

The preference key `page.<feature>.layout` and the `feature` string in
the table are **frozen** once shipped.

## Account scope: a feature's shared settings

Current members:

| Feature setting | Code home | Store | Gate |
|---|---|---|---|
| KPI grade thresholds | `features/kpi/` (service + router + ThresholdsDialog) | `account_settings["kpi_thresholds"]` | `can_manage_account` OR `can_manage_config_all` |
| Scorecard rules + pillar caps | `features/scorecards/config_router.py` + `/scorecard-rules` page | scorecards' own rows | `can_manage_config_all` (owner seeded; route/nav additionally module-masked safety/hr via featureCatalog) |

Note: `can_manage_config_all` itself is NOT module-masked (it spans
features across modules); a member PAGE may still mask with its
feature's modules, as scorecard rules does.

### Opt-in recipe (next feature setting)

1. Keep the config's logic in the feature's OWN folder: defaults in
   code, tolerant merge over them (unknown/invalid stored keys fall
   back — see `get_kpi_thresholds` for the reference shape).
2. Store under a feature-owned `account_settings` key (or the feature's
   own rows when the shape outgrows a KV blob).
3. Gate writes with `require_permission("can_manage_config_all")` (add
   `can_manage_account` via `require_permission_any` only when
   preserving a pre-family gate, as KPI does); mirror the same check on
   the UI affordance.
4. Add ZERO new permissions and ZERO new tables. If the setting seems
   to need per-role values, it's either view arrangement (→ role scope)
   or it violates the blast-radius rule — stop and reconsider.

## Invariants (tested)

* `account_id` comes only from the JWT — a hostile tenant can only
  address their own empty namespace (`tests/test_page_layouts.py`).
* The own-role wall holds under every grant combination; delegation and
  revocation through the matrix take effect via cache invalidation.
* An invalid team default degrades to the shipped layout, never to a
  crash or a partial apply (`pageLayoutConfig.test.ts`).
* Legacy permission keys are migrated, not aliased —
  `migrate_config_perm_keys` rewrites stored rows and is idempotent.
