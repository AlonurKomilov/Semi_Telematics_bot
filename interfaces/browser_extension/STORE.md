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

Requires a 4truck account. The panel never asks for a password: press Connect, confirm
once on 4truck.us, done. The panel can read what it shows and nothing else — it cannot
change anything in your account, and you can disconnect it any time from your 4truck
profile.

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
- Personally identifiable information — the person's display NAME, read from
  `/extension/me` to label the avatar menu. The extension never sees an email or a
  password (those are typed on 4truck.us, not in the panel) — but a name is PII, so the
  box stays CHECKED. Under-declaring is what gets an item pulled.
- Authentication information — a sign-in token, stored locally, received from 4truck after
  the person confirms on 4truck.us.
- Location — of the user's *vehicles*, read from 4truck; the extension does not access the
  user's own device location.
- Not sold, not used for ads, not shared with third parties; used only to display the
  user's own 4truck data.

**Screenshots (≥1, exactly 1280×800, 24-bit PNG):** take any capture — a full window with
Google Maps beside the panel is the picture that explains the product — drop the PNGs in
`versions/`, then `python3 store_screenshot.py versions/<file>.png [--focus right]` →
`versions/store-screenshots/`. It scales to cover and centre-crops (portrait captures
go on a dark canvas), and strips alpha.

**Privacy policy URL:** https://4truck.us/privacy
**Visibility:** Unlisted (only people with the link can install) — right for a B2B tool.

## The id

The store generated the package's key when the item was created — item id
`iihobedpipecckgmgegbhdpkmebabinn`, and that is the only id there is. Its PUBLIC half
(Package → **View public key**) sits in `public/manifest.json` as `key`, so a sideloaded
build computes the same id; `/extension/info` derives the id from that key. Nobody keeps a
private key. (A `key.pem` inside the zip is the pre-2020 method — the store ignores it.)

## Uploading a new version

1. Bump `version` in `public/manifest.json` and `package.json` (the store refuses a
   version it has already seen, drafts included).
2. `npm run build`, then `python3 build_packages.py` → `versions/4truck-extension-store-<v>.zip`
   (manifest without `key`, for the store) and `versions/4truck-extension-sideload-<v>.zip`
   (with the key, for Load unpacked). Earlier packages move to `versions/_archive/`.
3. Developer Dashboard → the item → **Package → Upload new package** → Submit for review.

## Review access

The panel is behind a login AND the consent step happens on the dashboard, so the
reviewer signs in for real — on a real account, with whatever the role permits. The
account is made by a script that resolves the account's EFFECTIVE permissions for the
role and prints every write flag the reviewer will hold before anything is written:

    python3 -m scripts.review_user --account <id> --company <CODE> --email <yours>            # role fleet
    python3 -m scripts.review_user ... --role driver --trucks 142,143,220                     # narrowest
    python3 -m scripts.review_user ... --apply

- **Role: fleet** (owner's decision). The extension is a desk tool for the people who run
  vehicles, and the reviewer should see it as they will. Fleet carries write permissions on
  the dashboard; the script lists them under EXPOSURE. The extension's own token cannot use
  any of them — only the dashboard session can. `--role driver` is the no-write alternative
  (needs `--trucks`, sees only those units).
- One company only, email pre-verified, random password printed once. Owner/admin never.
- Use an email you control: "forgot password" mails a reset link there.
- Before handing it over, sign in as that user and walk every sidebar item, the Alerts
  inbox and the AI assistant — whatever you see, the reviewer sees.

Retire it the day the item is approved — this also denylists every token it was issued,
which deactivation alone would not:

    python3 -m scripts.review_user --account <id> --email <yours> --delete --apply

**Additional instructions** (492 of the 500 characters allowed):

```
LIVE account: the vehicles and positions are real, and a change would affect a real company. Please look only — do not edit or delete anything.

1. Click the 4truck icon in the toolbar to open the side panel.
2. Press "Connect to 4truck" — a 4truck.us tab opens.
3. Sign in with the credentials above, then press Connect to confirm.
4. The panel lists the vehicles and shows them live on the map. Click one, then "Open in Google Maps".

The panel is read-only and sees vehicle positions only.
```
