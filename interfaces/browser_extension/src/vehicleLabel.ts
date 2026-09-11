/**
 * A vehicle as ONE line: its unit, and the company when that is worth
 * saying.
 *
 * Four surfaces render this pair — the map's card and its list, the
 * inventory card and its list — and all four ellipsise it in a 320px
 * column, which is where the name a person is hunting for gets cut.  The
 * text they show is styled in two spans (the company is muted), so the
 * string a `title` owes them cannot be read back off the DOM: it has to
 * be composed again.  Composing it HERE means the tooltip cannot drift
 * from the text it explains, and the separator is decided once.
 */
export function vehicleLine(name: string, company?: string | null, showCompany = true): string {
  return showCompany && company ? `${name} · ${company}` : name;
}
