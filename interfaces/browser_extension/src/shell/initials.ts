/** What the avatar shows: two letters of the name, else one of the email, else the product's "4". */
export function initialsOf(name?: string | null, email?: string | null): string {
  const n = (name ?? '').trim();
  if (n) {
    const parts = n.split(/\s+/).filter(Boolean);
    const first = parts[0]?.[0] ?? '';
    const last = parts.length > 1 ? parts[parts.length - 1][0] : '';
    return (first + last).toUpperCase();
  }
  const e = (email ?? '').trim();
  if (e) return e[0].toUpperCase();
  return '4';
}
