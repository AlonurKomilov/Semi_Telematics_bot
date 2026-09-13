/** The customer's plan cards, from the plan table — pure helpers so the
 *  page stays a renderer and the rules are testable. */

export interface CustomerPlan {
  tier: string;
  label: string;
  price_monthly_cents: number;
  base_vehicles: number;
  extra_vehicle_cents: number;
  everything: boolean;
  included: string[];
  quotas: Record<string, number>;
  public: boolean;
  /** hidden from everyone else; on this page because it was offered to this account */
  offered?: boolean;
  current: boolean;
}

/** On this account's page for a reason it can act on: sold to all, or offered here. */
export const onOffer = (p: CustomerPlan): boolean => p.public || !!p.offered;

export const money = (cents: number): string =>
  cents % 100 === 0 ? `$${cents / 100}` : `$${(cents / 100).toFixed(2)}`;

/** The plans on offer here that include *featureId* — where an Upgrade
 *  for it can go.  A plan that includes everything always qualifies. */
export function plansIncluding(featureId: string, plans: CustomerPlan[]): CustomerPlan[] {
  return plans.filter((p) => onOffer(p) && (p.everything || p.included.includes(featureId)));
}

/** The bullet lines of a card: what the plan includes in the reader's
 *  words (``label`` names a catalog id), then its quotas. */
export function featureLines(
  plan: CustomerPlan,
  label: (id: string) => string,
  words: { everything: string; unlimitedUsers: string; users: (n: number) => string; unlimitedCompanies: string; companies: (n: number) => string; trucks: (n: number) => string; extra: (price: string) => string },
): string[] {
  const lines: string[] = [];
  if (plan.base_vehicles > 0) lines.push(words.trucks(plan.base_vehicles));
  if (plan.extra_vehicle_cents > 0) lines.push(words.extra(money(plan.extra_vehicle_cents)));
  if (plan.everything) lines.push(words.everything);
  else lines.push(...plan.included.map(label));
  const u = plan.quotas.max_users ?? 0;
  lines.push(u === 0 ? words.unlimitedUsers : words.users(u));
  const c = plan.quotas.max_companies ?? 0;
  lines.push(c === 0 ? words.unlimitedCompanies : words.companies(c));
  return lines;
}

/** What an account already has that a plan would not hold.
 *
 *  A quota is enforced when something is CREATED, so moving to a
 *  smaller plan does not delete anything — it freezes what is over the
 *  line. That is a fair rule, and an unfair surprise if the customer
 *  only meets it after paying. So the card says it before the button.
 *
 *  Zero means unlimited, and the current plan is never warned about:
 *  whatever it holds today, it holds.
 */
export function overQuotaLines(
  plan: CustomerPlan,
  have: { users: number; companies: number },
  words: { users: (allowed: number, have: number) => string;
           companies: (allowed: number, have: number) => string },
): string[] {
  if (plan.current) return [];
  const out: string[] = [];
  const u = plan.quotas.max_users ?? 0;
  if (u > 0 && have.users > u) out.push(words.users(u, have.users));
  const c = plan.quotas.max_companies ?? 0;
  if (c > 0 && have.companies > c) out.push(words.companies(c, have.companies));
  return out;
}
