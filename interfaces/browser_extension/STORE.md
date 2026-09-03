# Chrome Web Store listing — copy from here

The product is 4truck — vehicle management. The extension is 4truck in a side
panel; Live Map is the FIRST feature in it, not the definition of it. Every
new panel feature gets one bullet under "What it shows today" and nothing else
in this file changes. Permissions and data-usage describe what the package
actually does TODAY — the review compares them with the code, so widen them
only in the same upload that widens the code.

**Name:** 4truck
**Summary (132 chars max, comes from `manifest.json` `description`):**
4truck vehicle management in a side panel, beside whatever you are working on.
**Category:** Workflow & Planning
**Language:** English
**Official URL:** https://4truck.us

**Description:**
4truck for Chrome brings your 4truck account into a side panel, beside whatever you are
working on — Google Maps, a load board, email, your TMS — so you never switch tabs to
answer a question about your vehicles.

What it shows today
• Live map — every vehicle's position, status and fuel, moving as it moves. Pick one to
  open its spot or directions in Google Maps with one click.

More of 4truck reaches the panel over time; each addition appears in this list.

Requires a 4truck account. Sign in inside the panel with your 4truck email and password.
The panel can read what it shows and nothing else — it cannot change anything in your
account, and you can revoke it any time from your 4truck profile.

**Single purpose:** A side panel for the signed-in user's 4truck vehicle-management account.

**Permission justifications:**
- `sidePanel` — the extension IS a side panel; that is the only UI it has.
- `storage` — keeps the sign-in token between browser sessions so the user is not asked to
  sign in every time the panel opens.
- Host `https://api.4truck.us/*` — the 4truck API the panel reads its data from.
- Host `https://www.google.com/maps/*` — to open a chosen vehicle's position or directions
  in a Google Maps tab the user already has open, instead of opening a new tab each time.

**Remote code:** No. All code is bundled in the package.

**Data usage (Privacy tab):**
- Authentication information (email/password sent to 4truck to sign in; a token stored locally).
- Location — of the user's *vehicles*, read from 4truck; the extension does not access the
  user's own device location.
- Not sold, not used for ads, not shared with third parties; used only to display the
  user's own 4truck data.

**Privacy policy URL:** https://4truck.us/privacy
**Visibility:** Unlisted (only people with the link can install) — right for a B2B tool.

## Uploading a new version

1. Bump `version` in `public/manifest.json` and `package.json` (the store refuses a
   version it has already seen, drafts included).
2. `npm run build`, then `python3 scripts_build_store.py --update` — no `key.pem`; the
   store knows the id from the first upload. Without `--update` the zip carries the key,
   which is right ONLY for the very first upload of a brand-new item.
3. Developer Dashboard → the item → **Package → Upload new package** → Submit for review.
