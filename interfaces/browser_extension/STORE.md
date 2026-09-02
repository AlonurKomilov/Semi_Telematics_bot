# Chrome Web Store listing — copy from here

**Name:** 4truck
**Summary (132 chars max):** Your fleet's trucks, live, in a side panel beside whatever you're working on.
**Category:** Workflow & Planning
**Language:** English

**Description:**
4truck for Chrome puts your fleet's live map in a side panel — beside Google Maps, a load
board, or email — so you never switch tabs to answer "where is unit 103?". See every truck's
position, status and fuel, watch them move, and jump to Google Maps for satellite view or
directions with one click.

Requires a 4truck account. Sign in inside the panel with your 4truck email and password.
The panel can read your fleet's live positions and nothing else — it cannot change anything
in your account, and you can revoke it any time from your 4truck profile.

**Single purpose:** Show the signed-in user's fleet live map in a side panel.

**Permission justifications:**
- `sidePanel` — the extension IS a side panel; that is the only UI it has.
- `storage` — keeps the sign-in token between browser sessions so the user is not asked to
  sign in every time the panel opens.
- Host `https://api.4truck.us/*` — the 4truck API the panel reads live positions from.
- Host `https://www.google.com/maps/*` — to open a chosen truck's position or directions in a
  Google Maps tab the user already has open, instead of opening a new tab each time.

**Remote code:** No. All code is bundled in the package.

**Data usage (Privacy tab):**
- Authentication information (email/password sent to 4truck to sign in; a token stored locally).
- Location — of the user's *vehicles*, read from 4truck; the extension does not access the
  user's own device location.
- Not sold, not used for ads, not shared with third parties; used only to display the fleet map.

**Privacy policy URL:** https://4truck.us/privacy
**Visibility:** Unlisted (only people with the link can install) — right for a B2B tool.
