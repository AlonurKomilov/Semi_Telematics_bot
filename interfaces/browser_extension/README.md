# 4truck browser extension

A Chrome side panel for 4truck beside whatever you are working on. Features register
in `src/shell/registry.ts`; Live Map is the first.
Fourth client, same layer as `dashboard`, `miniapp`, `system_dashboard`.

## Build and sideload

    npm install
    npm run build          # → dist/
    chrome://extensions → Developer mode → Load unpacked → dist/

Packages for shipping: `python3 build_packages.py` writes `versions/4truck-extension-store-<v>.zip`
(Web Store) and `versions/4truck-extension-sideload-<v>.zip` (Load unpacked) and moves earlier
versions to `versions/_archive/`. The folder is git-ignored. Listing text and the upload
recipe: [STORE.md](STORE.md).

`VITE_API_BASE` defaults to `https://api.4truck.us` (nginx serves the API at that host's root); set it for another host.

## Server side

- CORS: the extension has ONE id, the Chrome Web Store's:

      chrome-extension://iihobedpipecckgmgegbhdpkmebabinn

  The store generated the package's key when the item was created and keeps the
  private half; `manifest.json` carries the public half (Package → View public key), so
  a sideloaded build and the store build compute the same id, and `/extension/info`
  derives it from that key rather than repeating it. Add that origin to
  `CORS_ALLOWED_ORIGINS` once, for every customer. It is the extension package's id,
  not a user's or an account's: tenancy comes from the login token, never from the
  extension.
- The panel holds no credentials. **Connect to 4truck** opens
  `https://dash.4truck.us/extension/connect?state=<one-time>` (`VITE_DASHBOARD_BASE`
  overrides the host); the person signs in there if needed and confirms; the page calls
  `POST /extension/connect` (cookie session + `X-Requested-With`, the ONLY place an
  `aud=extension` token is minted) and hands the token to the service worker with
  `chrome.runtime.sendMessage` (`externally_connectable` = 4truck origins only). The worker
  accepts it only with the matching state, from a 4truck origin, and only if the token is
  the scoped kind — `src/connect.ts`. The token carries
  `scope=[can_location_map, can_location_vehicle]`; every other permission reads False for
  it. Every connection sends the person a sign-in notice with **Disconnect this session**,
  and the session shows as **Browser extension** in Active Sessions. Password logins
  refuse `client: "extension"` outright.

## Security posture

Signed store build (no remote code — MV3) · no content scripts, so a page can never
reach the panel · no password field, ever — consent happens on 4truck.us with the URL bar
in view · token in `chrome.storage`, isolated from pages and other extensions · every API
call goes out with `credentials: 'omit'`, so the dashboard cookie behind the panel is never
its fallback · token scoped to the live map · session visible, announced and revocable.
Host permissions are only `api.4truck.us` and `www.google.com/maps`.
