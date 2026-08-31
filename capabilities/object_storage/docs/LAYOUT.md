# Object storage: the tenant file layout

**This document is the law for where a tenant file goes on disk and in a
customer's connected Drive.** Read it before adding any code that calls
`ObjectStorage.put`. A layout mistake is not a cosmetic bug: the same
tree is mirrored into the customer's own Google Drive, where they browse
it by hand and read every top-level folder as one of their businesses.

Related: [config.md](config.md) (where settings live),
[warehouse.md](warehouse.md) (the other domain SSOT).

---

## The shape

```
data/userdata/                              ← OBJECT_STORE_ROOT
  account-{id}/
    {COMPANY DISPLAY NAME}/                 ← e.g. "PREMIER TRUCKING GROUP INC"
      camera-images/
      parking-maps/
      inspections/{inspection_id}/
      work-orders/{YYYY}/{MM-month}/WO-{id}_truck{n}_{date}_{vendor}/
      applications/{REFERENCE}/
      branding/                             ← logo-{company_id}.ext, banner-{company_id}.ext
      drivers/user-{user_id}/
        _archive/{YYYY-MM-DD}/user-{user_id}/
      vehicles/{unit_number}/
        documents/                            ← registration, cab card, title,
          _archive/                              …and the superseded ones
        work-orders/WO-{id}_{date}_{vendor}/  ← that truck's repair invoices
      vehicles/_archive/{YYYY-MM-DD}/{unit_number}/
        documents/  work-orders/              ← a RETIRED TRUCK, whole
      work-orders/{YYYY}/{MM-month}/          ← ONLY work orders with no vehicle
    _generic/                               ← holding pen: company unresolved (a bug)
    _account/                               ← account-level data (by design)
      knowledge/
      inspection-templates/
```

**Two archives, and they are not the same act.** Archiving a
*document* moves one superseded paper into
`vehicles/{unit}/documents/_archive/` — last year's registration once
this year's is filed — and the truck stays exactly where it is.
Archiving a *vehicle* moves the whole `vehicles/{unit}/` folder, papers
and work orders together, into the dated
`vehicles/_archive/{date}/{unit}/` tree, because the thing that left is
the truck.

The first version collapsed the two: it moved only the documents
subfolder into the truck archive, which archived a vehicle by touching
its paperwork and left its work orders behind under a live truck that
no longer existed.

**Everything about one truck lives under that truck.** Its papers and
its repair invoices are siblings in `vehicles/{unit}/`, because that is
how a carrier asks the question — "what has unit 110 cost me, and is
its insurance current?" — and a Drive folder is browsed, not queried.
Work orders used to file by calendar month across the whole account,
which answers "what did we spend in April"; the cost report answers
that better, on data no folder tree can beat.

A work order with **no vehicle** (shop supplies, a bulk parts invoice)
keeps the dated `work-orders/{year}/{month}/` tree, which is now what
that tree is FOR. Deliberately not the `_generic` pen: that means "no
company could be established", and here the company is known — only the
truck is missing, and inventing one would be worse than filing by date.

**Vehicle folders are named by UNIT NUMBER, not registry id** — this
tree is browsed by hand in the customer's Drive, and `vehicles/6862/`
reads as a truck where `vehicle-16/` reads as a database.  The number
is sanitized like a company name (user data on a path).  Two costs,
accepted: a renamed truck keeps its old folder until its documents are
next touched, and a reused number lands in the retired truck's folder
only if that truck was never archived — archiving moves the folder to
`vehicles/_archive/{date}/{unit}/` (the driver-archive recipe: physical
move + `move_vehicle_documents_bucket` row rewrite), and restore moves
it back.  Helpers: `vehicle_docs_bucket` / `vehicle_docs_archive_bucket`
in `capabilities/object_storage/paths.py`.

`OBJECT_STORE_ROOT` (`infra/config.py`) is read **once at import**.
Changing it needs a process restart, and tests must set it by plain
assignment before importing `adapters.storage` — see
[the test-isolation rule](#tests-must-not-write-here).

### Moving the code away from the data

Stored paths are **relative** (`data/userdata/account-N/…`) and the
resolver builds candidates from the code's own location. Move the
checkout — a worktree, a deploy to `/srv`, a second clone — and every
one of those paths points into a tree with no files in it. The app
looks healthy; every document 404s.

Setting an absolute `OBJECT_STORE_ROOT` does **not** fix that by
itself, and this is the trap: it changes where *new* writes go, while
every existing row stays relative. A "check a document still loads"
test on the original machine passes either way.

So the resolver also rebases a relative stored path onto an absolute
`OBJECT_STORE_ROOT`, tried *after* the project-relative candidates so a
checkout that owns its data still wins. With that, either arrangement
works:

* **data inside the checkout** — leave the root relative (the default).
  Paths stay portable, nothing hardcodes a machine.
* **data on a mounted volume or left behind by a move** — set the root
  absolute. Old and new rows both resolve.

What you must not do is move the code, leave the data, and assume an
absolute root alone covers it — check `TestTheDataRootSurvivesMovingTheCode`
for what is actually guaranteed.

### Two rules that are not negotiable

**1. Nothing but a company folder or `_generic` may sit directly under
`account-{id}/`.**

The customer reads that level as "my businesses". A bare
`applications/` beside five real company names reads like a sixth
company. That exact fallback — `f"applications/{reference}"` when a
recruiter link named no company — is where 939 stray files accumulated
before it was removed.

**2. The top segment is the company's DISPLAY NAME, sanitised — not its
code, not its id.**

Use `sanitize_company_folder(display_name)` from
`capabilities/object_storage/paths.py`, or `resolve_company_folder(db,
account_id, company_code)` when you hold a code. A user browsing Drive
should see `PREMIER TRUCKING GROUP INC`, never `PTG` or `company-1`.

Consequence worth knowing: **renaming a company splits its folder.** The
old folder keeps the old files and new writes land in a new one. The
repair script's Phase E re-derives the company for every stored path and
moves what disagrees, so run it after a rename.

---

## `_generic` — the holding pen

`GENERIC_COMPANY_FOLDER = "_generic"` in
`capabilities/object_storage/paths.py`. It is where a write goes when no
company can be established for it.

It is a **bug report, not a second home**. Every write that lands there
logs a warning naming the account. If files accumulate, a writer is
losing the company somewhere upstream — go fix that writer.

Three properties, each deliberate:

* **Leading underscore.** It must not read like a business. Its
  predecessor was named `unnamed-company`, which read exactly like one,
  and quietly collected 749 camera photos over a year.
* **Exactly one pen.** Renaming the constant once left the old folder
  standing beside the new one — two pens is worse than one badly-named
  pen. The relocation moves unresolved files into the canonical pen
  rather than leaving them behind.
* **Never a guess.** A file whose company is genuinely unknown belongs
  here, not under a company picked by probability. A wrong company reads
  as correct and is far harder to notice later.

---

## `_account` — data with no company, by design

`ACCOUNT_LEVEL_FOLDER = "_account"`, same module.

Not everything is company-scoped. A knowledge-base article and an
inspection template serve the whole account; forcing them under one
company would be a lie about who owns them. They still may not sit at
the account root, for the same reason as everything else.

**`_account` and `_generic` are deliberately different folders**, and
the difference is intent:

| | meaning | when you see files here |
|---|---|---|
| `_generic` | we could not work out the company | a writer is broken — go fix it |
| `_account` | there is no company, by design | correct, nothing to do |

Collapsing them would make the bug report unreadable: real
account-level data would drown the writers that genuinely failed.

---

## Finding the company for a vehicle's file

The vehicle registry (`vehicles`) is the SSOT for which company owns a
unit. `Database.company_code_for_unit(account_id, unit_number)` is the
live-path lookup; it is account-scoped and returns `""` rather than
guess.

**Prefer the telematics id over the unit number.** A unit number is a
LABEL and is reused: one live account runs two different trucks both
called `103`, different VINs, one in G1 and one in OSY. Matched by name
their photos are one ambiguous pile; matched by telematics id they
separate cleanly. Any code resolving a vehicle→company from a stored
record should try `telematics_ref` first and the unit number only as a
fallback.

**Match ACTIVE registry rows only.** Counter-intuitive but measured:
including retired rows resolves *fewer* files, because a unit number
that has belonged to two companies over its life becomes ambiguous. The
active row is the current truth about who owns that number.

### The display key and the wire key are different

Records that travel from an integration carry both:

| key | meaning | value when unknown |
|---|---|---|
| `_org` | the wire code storage files by | `""` |
| `company` | the label a report prints | `"?"` |

They are **not interchangeable.** `"?"` is a legal folder-name
character, and a save path that read the display key would file real
trucks under a directory called `?`. A permission or scope filter must
key on `_org` too — reading the label there let unattributed rows
through to every company-restricted subscriber.

---

## Tests must not write here

`accounts.id` starts at **10000001** by schema design, so the first
account a fresh test database creates shares a folder with the first
real one. With `OBJECT_STORE_ROOT` unset, the suite writes into a live
customer's tree — it did, for months.

`conftest.py` assigns `OBJECT_STORE_ROOT` to a temp dir before the
first `adapters.storage` import. **Assignment, not `setdefault`**, so an
exported production path cannot win.

To check isolation: count `data/userdata` before and after a run; it
must not change.

---

## Repair tooling

Both read `DATABASE_URL` from `.env` and are **dry-run by default**.

| script | what it does |
|---|---|
| `scripts/relocate_userdata_layout.py` | Moves files into the correct company folder and updates the referencing rows in the same pass. Phases: branding, applications, legacy underscore buckets, placeholder rehoming, disk-driven branding rescue, applications off the account root. |
| `scripts/purge_test_artifacts.py` | Deletes test-suite stubs. Refuses anything referenced, anything at or over the genuine-content threshold, and anything outside a known-polluted directory. |

A file move and its row update always happen **together**, and the row
is updated only after the move succeeds.

**Never bulk-delete from a customer's connected Drive.** Ours is the
only storage automated cleanup may touch; their cloud keeps its copies.
Files already synced to Drive under an old path stay there after a
relocation — the scripts report this rather than reaching into the
customer's account.

---

## Adding a new file-writing feature

1. Resolve the company: `resolve_company_folder(db, account_id, code)`,
   or the registry when you only have a vehicle.
2. Compose `f"{company_folder}/{your-segment}"` as the bucket. Use a
   dashed segment name (`camera-images`, not `camera_images` — the
   underscore forms are pre-layout legacy).
3. Make the key unique per record. A key that is unique only per
   *entity* means each new record overwrites the last one's file: the
   camera key was `{truck}_{camera}.jpg` and 17,689 checks resolved to
   340 files.
4. Store the returned path on the row, and add the column to the
   reference sweep in `capabilities/object_storage/references.py` so
   retention and the purge can see it.
5. Never write to `account-{id}/` directly, and never invent a second
   holding pen. If the data has no company, decide WHICH kind of
   no-company it is: `ACCOUNT_LEVEL_FOLDER` when that is correct by
   design, `GENERIC_COMPANY_FOLDER` when you simply could not resolve
   one.

A bucket that is a bare literal — `store.put("knowledge", …)` — always
lands at the account root. The guard rejects those; two shipped that
way before it existed.

`tests/test_object_storage_layout.py` enforces rules 1, 2 and 5.
