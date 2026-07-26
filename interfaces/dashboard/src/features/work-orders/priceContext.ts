// "Is this price normal?" for a work-order part line, answered from
// the account's OWN buying history.
//
// This is the display half of the price-intelligence idea. Market-wide
// ranges need three consenting fleets before anything can publish;
// your own purchase history needs nothing, and answers the same
// question at the same moment — while you're entering the invoice,
// when you can still call the shop.
//
// The band is p25–p75, computed server-side the same way market ranges
// are, so when market data does exist the two read alike side by side.

/** Per-part history, keyed by normalized part name. */
export interface PriceContext {
  buys: number;
  low: number;
  high: number;
  last_price: number;
  cheapest_vendor: string;
  cheapest_price: number;
  months: number;
}

export type PriceContextMap = Record<string, PriceContext>;

/** Mirrors the server's `part_name_key`: trim, collapse inner
 *  whitespace, casefold. Must match, or lookups silently miss. */
export function priceKey(name: string): string {
  return (name || '').trim().replace(/\s+/g, ' ').toLowerCase();
}

export type PriceVerdict = 'above' | 'below' | 'typical';

/** Where this unit price sits against what you usually pay.
 *  A price at or inside the band is 'typical' — the band edges are
 *  inclusive so a price exactly equal to p75 isn't scolded. */
export function verdictFor(unitCost: number, ctx: PriceContext): PriceVerdict {
  if (!Number.isFinite(unitCost) || unitCost <= 0) return 'typical';
  if (unitCost > ctx.high) return 'above';
  if (unitCost < ctx.low) return 'below';
  return 'typical';
}

const money = (n: number): string =>
  `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })}`;

/** The one-line hint under a part row. Short by design — the detail
 *  lives in the tooltip. */
export function summaryFor(ctx: PriceContext): string {
  const range = ctx.low === ctx.high
    ? money(ctx.low)
    : `${money(ctx.low)}–${money(ctx.high)}`;
  return `You usually pay ${range}`;
}

/** The hover detail: how solid this is, and where it was cheapest. */
export function detailFor(ctx: PriceContext): string {
  const bits = [
    `${ctx.buys} purchase${ctx.buys === 1 ? '' : 's'} in the last ${ctx.months} months`,
    `last paid ${money(ctx.last_price)}`,
  ];
  if (ctx.cheapest_vendor) {
    bits.push(`cheapest ${money(ctx.cheapest_price)} at ${ctx.cheapest_vendor}`);
  }
  return bits.join(' · ');
}

/** Lines priced above what you usually pay — what a summary counts. */
export function overpricedLines(
  lines: Array<{ part_name: string; unit_cost: number }>,
  contexts: PriceContextMap,
): Array<{ part_name: string; unit_cost: number; ctx: PriceContext }> {
  const out = [];
  for (const l of lines) {
    const ctx = contexts[priceKey(l.part_name)];
    if (ctx && verdictFor(l.unit_cost, ctx) === 'above') {
      out.push({ ...l, ctx });
    }
  }
  return out;
}
