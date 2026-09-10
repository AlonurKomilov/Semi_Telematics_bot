# Versioning — read this BEFORE touching a version number

Four digits, agreed with the owner on 2026-09-10:

```
0  .  5  .  0  .  0
│     │     │     └── FIX      — nothing gained a capability
│     │     └──────── COMPONENT — an existing feature gained a part
│     └────────────── FEATURE   — the panel gained (or lost) a feature
└──────────────────── EXTENSION — the product became a different thing
```

Chrome accepts one to four dot-separated integers (0–65535 each), so four
is legal in `manifest.json`; npm accepts it in `package.json` too — both
were tested, not assumed.

## Which digit moves

| Digit | Moves when | From this project |
|---|---|---|
| **1 — Extension** | the product becomes a different thing. `1.0.0.0` is the first stable public release. | never yet |
| **2 — Feature** | the panel gains or loses a FEATURE — an entry in the header's switcher. | Inventory became a panel feature |
| **3 — Component** | an existing feature gains a part: a form, a section, an endpoint it calls, a new fact it shows. | the Add-item form; the map card listing contents; picking a vehicle moving Google's map |
| **4 — Fix** | everything that changes no capability: a bug, a layout correction, copy, a rename, a guard, a refactor. | the squeezed card; the marker anchor; the horizontal scrollbar |

**Cascade.** A digit moving resets everything to its right to 0.
`0.5.3.4` + a new feature → `0.6.0.0`.

**The highest one wins.** A commit that adds a component AND fixes four
things inside it is one COMPONENT bump, not a fix bump.

## The feature digit is pinned at 5 for now

The owner's rule, and it overrides the table above until it lapses:

> The second digit stays **5** while the panel has fewer than six
> features. When the sixth feature lands it becomes `0.6.0.0`, and from
> there the digit moves normally.

Today the panel has **two**: Live Map and Inventory. So a new feature
before the sixth does NOT move the second digit — it moves the third,
like a component. Nothing about the third and fourth digits changes.

Why 5 and not 2: nothing has been uploaded to the Chrome Web Store yet,
so the number is still ours to choose, and the owner chose a floor to
grow into rather than a count to start from.

## A version is SPENT once installed or uploaded

Not once built. A number that was named in a message but never installed
can be reused: delete its zips and rebuild at the same number.

**Going DOWN is not an update.** Chrome refuses to update an installed
extension to a lower version, so a renumbering downward (0.12.0 → 0.5.0.0
was one) means removing the sideload and loading it fresh. Say so in the
closing message when it happens.

## What the number does NOT say

Whether the API changed and `make restart-clean` is needed. That is the
server's state, not the extension's, and no digit can carry it — the
closing message says it, every time, in words.

## The recipe

1. Read this file. (The extension's `CLAUDE.md` makes that mandatory —
   the number was bumped by feel four times in one day before these
   rules existed, and the minor digit walked from 0.7 to 0.12 saying
   nothing.)
2. Bump `version` in `public/manifest.json` AND `package.json` — the same
   string in both. The store refuses a version it has seen, drafts
   included.
3. `npm run build && python3 build_packages.py` → both zips, older ones
   archived into `_archive/`.
4. The closing message names BOTH files, says "upload the store one", and
   says whether a restart is needed.
