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
  current: boolean;
}

export const money = (cents: number): string =>
  cents % 100 === 0 ? `$${cents / 100}` : `$${(cents / 100).toFixed(2)}`;

/** The public plans that include *featureId* — where an Upgrade for it
 *  can go.  A plan that includes everything always qualifies. */
export function plansIncluding(featureId: string, plans: CustomerPlan[]): CustomerPlan[] {
  return plans.filter((p) => p.public && (p.everything || p.included.includes(featureId)));
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
