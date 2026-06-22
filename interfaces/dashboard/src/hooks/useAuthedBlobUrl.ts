import { useEffect, useState } from 'react';
import { apiFetch } from '../api/client';

/**
 * Fetch a binary asset from an authed API endpoint and expose it as a
 * blob URL the browser can render in <img>, <embed>, <iframe>, etc.
 *
 * Why this exists
 * ───────────────
 * Browsers do NOT send the Authorization header on element-driven
 * fetches (the src attribute of <img> / <iframe> / <embed>).  Our
 * /api/knowledge/articles/{id}/file endpoint requires get_current_user,
 * so a naive ``<img src="/api/...">`` will 401.  This hook bridges that:
 * it calls ``apiFetch`` (which DOES add the JWT), reads the bytes into
 * a Blob, and turns it into an object URL via ``URL.createObjectURL``.
 *
 * Cleanup
 * ───────
 * Object URLs leak until the page unloads if you don't revoke them, so
 * the hook revokes on unmount + whenever the input URL changes.  Cancel
 * flag guards against the race where the user navigates away mid-fetch
 * (don't set state on an unmounted component).
 *
 * Behaviour
 * ─────────
 *   - returns ``null`` while loading or when ``url`` is empty/null
 *   - returns the object URL string once the blob is ready
 *   - swallows fetch errors (returns null) — caller can show a fallback
 */
export function useAuthedBlobUrl(url: string | null | undefined): string | null {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!url) { setBlobUrl(null); return; }
    let cancelled = false;
    let created = '';
    apiFetch(url)
      .then((res) => (res.ok ? res.blob() : null))
      .then((blob) => {
        if (cancelled || !blob || blob.size === 0) return;
        created = URL.createObjectURL(blob);
        setBlobUrl(created);
      })
      .catch(() => { /* swallow — caller renders a fallback */ });
    return () => {
      cancelled = true;
      if (created) URL.revokeObjectURL(created);
      setBlobUrl(null);
    };
  }, [url]);
  return blobUrl;
}
