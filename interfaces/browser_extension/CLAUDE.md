# Browser extension — rules that were learned the hard way

## Every change ships to BOTH channels, in the same session

The extension reaches people two ways: the sideload zip (`versions/…-sideload-<v>.zip`,
carries the manifest `key` so it takes the store's id) and the Chrome Web Store
(`versions/…-store-<v>.zip`, no `key`). They are built from the same tree by
`python3 build_packages.py`, so they never differ in code — only in which one the
owner has actually installed or uploaded.

**The rule:** a change to the panel or the overlay is not done when the sideload
zip is built. It is done when the owner has been handed the STORE zip too, with
its version number, and told to upload it. Between 0.4.2 and 0.4.7 the panel
changed six times while the store still served 0.4.0, and the owner tested a
store install against a sideload and read the difference as a bug. Same code
would have been the same behaviour.

### Which digit moves — READ `versions/CLAUDE.md` FIRST

**Mandatory, before touching any version number:** open
[versions/CLAUDE.md](versions/CLAUDE.md).  It holds the four-digit scheme
the owner agreed — extension · feature · component · fix — the cascade
rule, which digit a given change moves, and the pin currently holding the
feature digit at 5.

This is a rule and not a suggestion because the number was bumped by feel
four times in one day before the scheme existed, and the minor digit
walked from 0.7 to 0.12 while saying nothing about what had changed.  A
version is a message to the owner; guessing at it sends the wrong one.

The recipe lives there too.  What stays here, because it is about the
STORE rather than the number: if a store review is pending, the next
version waits for it or the owner cancels the pending review — say which.
Never delete the reviewer account (`test@premiertruckinggroup.com`)
between reviews.

## Reading the URL

Google's URL carries a zoom only on the map (`…12z`). Satellite and the globe
carry metres of visible ground (`…1966123m`); `cameraFromUrl` derives the zoom
from the canvas height, so measure first, parse second. A view neither shape
describes (3D pose, Street View) is shown on the switch as "3D view", never as
"loading…". First diagnostic for "overlay stuck loading": the URL's third field.

## Manifest

`web_accessible_resources[].matches` must end in `/*` — Chrome refuses the whole
package otherwise, saying only "Invalid match pattern". `content_scripts` and
`host_permissions` take a path and stay on `/maps/*`. `src/manifest.test.ts`
holds all three to their rules.

## One vehicle, one row

`/map/vehicles` can carry the same provider id twice when two registry rows
claim one telematics ref (it happened: registry ids 60 and 1003, one Samsara
id). The panel keys rows by id, so a duplicate id is a duplicate React key and
React leaves ghost rows behind that survive filtering. The list dedupes by id
before rendering; the server dedupes too. Both, because each guards a
different failure.
