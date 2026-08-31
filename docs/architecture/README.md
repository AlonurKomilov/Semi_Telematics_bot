# Architecture docs — the index, and the rule

**The rule (owner decision, 2026-08-31): a system's law lives where its
devs work, not here.**  A doc at root rots because nobody editing the
feature remembers it exists — the same reason tests moved next to the
code they guard (206 of 277 files were silently skipping when they
lived far away).  In-package law is the house pattern:
`capabilities/object_storage/LAYOUT.md` did it first.

This folder keeps only what belongs to NO package:

  * repo-wide conventions (this file, PERSONA naming, the permission
    matrix shape),
  * laws whose homes are so many that no package is honest to pick —
    reviewed per doc, opportunistically, on touch,
  * and THIS INDEX, so browsability survives the scattering.

## The index (update when a law moves or is born)

| system | the law lives at |
|---|---|
| Tenant file layout | `capabilities/object_storage/LAYOUT.md` |
| Tours (walkthroughs) | `capabilities/tour/ARCHITECTURE.md` |
| Warehouse | `docs/architecture/warehouse.md` |
| Config family | `docs/architecture/config.md` |
| Notifications spine | `docs/architecture/notifications.md` |
| Persona naming | `docs/architecture/PERSONA.md` |
| Permissions matrix | `docs/architecture/permissions-feature-matrix.md` |
| Bot topology | `docs/architecture/bot-topology.md` |
| Vendor/parts master data | `docs/architecture/vendor-parts-master-data.md` |
| AI copilot / import | `docs/architecture/ai-copilot-*.md`, `ai-import-assistant.md` |
| Alert-DM migration record | `docs/architecture/alert-dm-migration.md` |

## Writing a law that survives a server split

Open every law with two things, so a future "move feature X to its own
server" day starts from a table of cut-lines instead of prose
archaeology:

  * **a Homes table** — which packages/layers the system spans;
  * **a Data-it-touches line** — tables, stores, caches.  Data
    coupling, not code location, is what makes extraction expensive
    (worked example: the tour backend reads the shared
    ``activity_events`` table — extracted, that SQL becomes a network
    call someone must design).

Extraction protocol: lifting package P → its in-package docs and tests
travel free → grep this index + the tree for P → each law's Homes
table shows what splits, its contracts section becomes the API spec,
its data line becomes the decoupling work list.
