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
- Login sends `client: "extension"` to `/auth/login` and receives a token with
  `aud=extension` and `scope=[can_location_map, can_location_vehicle]`. Every other
  permission reads False for that token. It appears in Active Sessions as
  **Browser extension** and can be revoked there.

## Security posture

Signed store build (no remote code — MV3) · no content scripts, so a page can never
reach the panel · token in `chrome.storage`, isolated from pages and other extensions ·
token scoped to the live map · session visible and revocable. Host permissions are only
`api.4truck.us` and `www.google.com/maps`.
