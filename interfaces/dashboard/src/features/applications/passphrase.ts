/**
 * A suggested DQF export passphrase.
 *
 * A SUGGESTION, never a setting. This fills the input so an administrator
 * can read it, keep a copy, and then press "Set passphrase" themselves.
 * Nothing here saves, and nothing here runs on its own — the derived
 * default stays in place until a human commits a replacement. That is
 * deliberate: `default_passphrase` is deterministic, so silently rotating
 * every account onto a random value would change the password on files
 * already sitting in carriers' cloud storage and lock out anyone holding
 * the old one.
 *
 * WHY IT IS GENERATED HERE, IN THE BROWSER. The value is not a passphrase
 * until the administrator commits it. Generating it client-side means a
 * suggestion they discard never reaches the server, is never logged, and
 * is never mailed — only the one they actually choose is.
 */

// 32 characters, which is what makes this unbiased: five random bits index
// the alphabet exactly, so no modulo folding and no rejection loop.
//
// I and O are dropped because they collide with 1 and 0 when this is read
// off a screen and typed somewhere else. L is KEPT: it collides with 1
// only in lowercase, and this alphabet is upper-case throughout. That is
// not a free choice — dropping it too would leave 31 symbols, and 31 is
// not a power of two, so every character would need either modulo folding
// (biased) or a rejection loop. Exactly 32 is the property being bought.
const ALPHABET = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';

const GROUPS = 5;
const GROUP_LEN = 4;

/**
 * A 20-character passphrase in five hyphenated groups, e.g.
 * `K7M2-QX9P-4RTV-H3JW-N8ZB`.
 *
 * 20 characters from a 32-symbol alphabet is 100 bits of entropy. The
 * hyphens are presentation only and are part of the stored value, so what
 * the user reads is exactly what they type back.
 */
export function generatePassphrase(): string {
  const bytes = new Uint8Array(GROUPS * GROUP_LEN);
  // crypto.getRandomValues, never Math.random: this value protects
  // applicants' Social Security Numbers, and Math.random is seeded
  // predictably enough to be reconstructed.
  crypto.getRandomValues(bytes);

  const chars = Array.from(bytes, (b) => ALPHABET[b & 31]);
  const groups: string[] = [];
  for (let i = 0; i < GROUPS; i += 1) {
    groups.push(chars.slice(i * GROUP_LEN, (i + 1) * GROUP_LEN).join(''));
  }
  return groups.join('-');
}

/** Shape check for tests and for the "did the user edit it?" comparison. */
export const PASSPHRASE_PATTERN = new RegExp(
  `^[${ALPHABET}]{${GROUP_LEN}}(-[${ALPHABET}]{${GROUP_LEN}}){${GROUPS - 1}}$`,
);
