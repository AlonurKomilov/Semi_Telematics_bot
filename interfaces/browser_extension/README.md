# 4truck browser extension

A Chrome side panel: the Live Map beside whatever you are working on.
Fourth client, same layer as `dashboard`, `miniapp`, `system_dashboard`.

## Build and sideload

    npm install
    npm run build          # → dist/
    chrome://extensions → Developer mode → Load unpacked → dist/

`VITE_API_BASE` defaults to `https://api.4truck.us` (nginx serves the API at that host's root); set it for another host.

## Server side

- CORS: add the extension's origin to `CORS_ALLOWED_ORIGINS` — `chrome-extension://<id>`
  (the id is shown on chrome://extensions after loading).
- Login sends `client: "extension"` to `/auth/login` and receives a token with
  `aud=extension` and `scope=[can_location_map, can_location_vehicle]`. Every other
  permission reads False for that token. It appears in Active Sessions as
  **Browser extension** and can be revoked there.

## Security posture

Signed store build (no remote code — MV3) · no content scripts, so a page can never
reach the panel · token in `chrome.storage`, isolated from pages and other extensions ·
token scoped to the live map · session visible and revocable. Host permissions are only
`api.4truck.us` and `www.google.com/maps`.
