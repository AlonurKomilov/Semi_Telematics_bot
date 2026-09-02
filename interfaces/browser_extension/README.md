# 4truck browser extension

A Chrome side panel: the Live Map beside whatever you are working on.
Fourth client, same layer as `dashboard`, `miniapp`, `system_dashboard`.

## Build and sideload

    npm install
    npm run build          # → dist/
    chrome://extensions → Developer mode → Load unpacked → dist/

`VITE_API_BASE` defaults to `https://api.4truck.us` (nginx serves the API at that host's root); set it for another host.

## Server side

- CORS: the extension has a PERMANENT id — `manifest.json` carries the public half of a
  signing key, so every install on every machine gets the same one:

      chrome-extension://bpfmimpagohdiafleecmpkkcglohcbge

  Add that origin to `CORS_ALLOWED_ORIGINS` once, for every customer. It is the
  extension package's id, not a user's or an account's: tenancy comes from the login
  token, never from the extension. The private key lives OFF the repo at
  `~/.4truck-extension/key.pem` on the server — it is what proves ownership of this id
  when the extension is first published to the Web Store, so it must not be lost or
  committed.
- Login sends `client: "extension"` to `/auth/login` and receives a token with
  `aud=extension` and `scope=[can_location_map, can_location_vehicle]`. Every other
  permission reads False for that token. It appears in Active Sessions as
  **Browser extension** and can be revoked there.

## Security posture

Signed store build (no remote code — MV3) · no content scripts, so a page can never
reach the panel · token in `chrome.storage`, isolated from pages and other extensions ·
token scoped to the live map · session visible and revocable. Host permissions are only
`api.4truck.us` and `www.google.com/maps`.
