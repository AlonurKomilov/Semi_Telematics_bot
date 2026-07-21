import { describe, it, expect } from 'vitest';
import { urlBase64ToUint8Array } from './push';

describe('urlBase64ToUint8Array', () => {
  it('decodes a b64url VAPID key back to its raw bytes', () => {
    // A 65-byte uncompressed P-256 point (0x04 || X || Y) like a real
    // application-server key, encoded the way the backend serves it.
    const raw = new Uint8Array(65);
    raw[0] = 0x04;
    for (let i = 1; i < 65; i++) raw[i] = (i * 7 + 3) % 256;
    const b64url = btoa(String.fromCharCode(...raw))
      .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    expect(b64url).toHaveLength(87);            // matches the server's shape

    const out = urlBase64ToUint8Array(b64url);
    expect(out).toHaveLength(65);
    expect(Array.from(out)).toEqual(Array.from(raw));
  });

  it('handles unpadded lengths', () => {
    for (const n of [1, 2, 3, 4, 5]) {
      const raw = new Uint8Array(n).fill(0xab);
      const b64url = btoa(String.fromCharCode(...raw))
        .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
      expect(Array.from(urlBase64ToUint8Array(b64url))).toEqual(Array.from(raw));
    }
  });
});
