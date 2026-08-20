/**
 * How the SHEET spells money.
 *
 * The board has its own speller ([board/shared.ts](../board/shared.ts))
 * because its inputs are typed numbers; here every cell value arrives
 * from DataGrid as `unknown`, and a null must read as "—" rather than
 * "$NaN".
 */
export function usd(v: unknown): string {
  if (v == null) return '—';
  return `$${Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;
}
