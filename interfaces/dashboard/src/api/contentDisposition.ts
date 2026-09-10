/**
 * The filename the SERVER chose, for a download fetched as a blob.
 *
 * A plain `<a href>` lets the browser read `Content-Disposition` itself
 * — but a download that needs a bearer token has to go through `fetch`,
 * and a blob URL carries no name at all.  Whatever is put in
 * `a.download` then wins absolutely, so a hardcoded string there
 * silently overrides everything the server said.
 *
 * Not hypothetical: the extension zip was served as
 * `4truck-extension-sideload-0.5.1.0.zip` and still landed in Downloads
 * as `4truck-extension (10).zip`, because the only button that fetches
 * it named the file itself.  One name for every build is how a folder
 * fills with copies nobody can tell apart — and, for a zip, how it
 * never earns a reputation with Windows Defender.
 *
 * The server is the single authority on the name; this reads it back.
 */

/** Percent-decode, but never fail: a malformed sequence yields the raw
 *  text rather than throwing away an otherwise usable name. */
function decode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/** A filename, never a path.  A header is remote input, and a name
 *  carrying separators has no legitimate meaning in a save dialog. */
function sanitize(name: string): string | null {
  const bare = name.replace(/\\/g, '/').split('/').pop()?.trim() ?? '';
  // Control characters would be invisible in the save dialog.
  // eslint-disable-next-line no-control-regex
  const clean = bare.replace(/[\u0000-\u001f\u007f]/g, '');
  if (!clean || clean === '.' || clean === '..') return null;
  return clean;
}

/**
 * Parse `Content-Disposition` into a filename, or null when the header
 * is absent, unreadable, or names nothing usable.
 *
 * Null is a real answer and every caller must handle it: the header is
 * NOT CORS-safelisted, so an API served from another origin without
 * `Access-Control-Expose-Headers` yields null here even though the
 * server set it correctly.
 */
export function filenameFromDisposition(header: string | null | undefined): string | null {
  if (!header) return null;
  // RFC 5987 `filename*` wins when both are present — that is the point
  // of it: plain `filename` is the ASCII fallback for older clients.
  const extended = /filename\*\s*=\s*([^;]+)/i.exec(header);
  if (extended) {
    const raw = extended[1].trim();
    // charset'language'percent-encoded-value
    const parts = raw.split("'");
    const value = parts.length >= 3 ? parts.slice(2).join("'") : raw;
    const out = sanitize(decode(value));
    if (out) return out;
  }
  const quoted = /filename\s*=\s*"([^"]*)"/i.exec(header);
  if (quoted) return sanitize(quoted[1]);
  const plain = /filename\s*=\s*([^;]+)/i.exec(header);
  if (plain) return sanitize(plain[1].trim());
  return null;
}
