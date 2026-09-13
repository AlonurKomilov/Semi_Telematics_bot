import { describe, expect, it } from 'vitest';
import { featureLines, money, overQuotaLines, plansIncluding, type CustomerPlan } from './planCards';

const plan = (over: Partial<CustomerPlan>): CustomerPlan => ({
  tier: 'x', label: 'X', price_monthly_cents: 0, base_vehicles: 0, extra_vehicle_cents: 0,
  everything: false, included: [], quotas: {}, public: true, current: false, ...over,
});
const words = {
  everything: 'Every feature and service',
  unlimitedUsers: 'Unlimited users', users: (n: number) => `Up to ${n} users`,
  unlimitedCompanies: 'Unlimited companies', companies: (n: number) => `Up to ${n} companies`,
  trucks: (n: number) => `${n} trucks included`, extra: (p: string) => `${p} per extra truck`,
};

describe('planCards — the customer sees the plan table, in their words', () => {
  it('money prints whole dollars plainly and cents when there are any', () => {
    expect(money(4900)).toBe('$49');
    expect(money(299)).toBe('$2.99');
    expect(money(0)).toBe('$0');
  });

  it('an Upgrade for a feature goes only to a public plan that includes it', () => {
    const plans = [
      plan({ tier: 'starter', included: ['vehicles'] }),
      plan({ tier: 'pro', everything: true }),
      plan({ tier: 'hidden', everything: true, public: false }),
    ];
    expect(plansIncluding('maintenance', plans).map((p) => p.tier)).toEqual(['pro']);
    expect(plansIncluding('vehicles', plans).map((p) => p.tier)).toEqual(['starter', 'pro']);
  });

  it('a card lists trucks, what is included, then quotas — unlimited when 0', () => {
    const lines = featureLines(
      plan({ base_vehicles: 10, extra_vehicle_cents: 299, included: ['vehicles', 'maintenance'], quotas: { max_users: 75, max_companies: 0 } }),
      (id) => id.toUpperCase(), words,
    );
    expect(lines).toEqual(['10 trucks included', '$2.99 per extra truck', 'VEHICLES', 'MAINTENANCE', 'Up to 75 users', 'Unlimited companies']);
    expect(featureLines(plan({ everything: true }), (id) => id, words)).toEqual(['Every feature and service', 'Unlimited users', 'Unlimited companies']);
  });
});

describe('overQuotaLines', () => {
  const plan = (over: Partial<CustomerPlan>): CustomerPlan => ({
    tier: 'starter', label: 'Starter', price_monthly_cents: 4900,
    base_vehicles: 10, extra_vehicle_cents: 299, everything: true,
    included: [], quotas: { max_users: 75, max_companies: 3 },
    public: true, current: false, ...over,
  });
  const words = {
    users: (a: number, h: number) => `users ${h}/${a}`,
    companies: (a: number, h: number) => `companies ${h}/${a}`,
  };

  it('names a limit the account is already past', () => {
    // The real case: 5 companies moving to a plan that allows 3.
    expect(overQuotaLines(plan({}), { users: 22, companies: 5 }, words))
      .toEqual(['companies 5/3']);
  });

  it('says nothing when everything fits', () => {
    expect(overQuotaLines(plan({}), { users: 22, companies: 2 }, words)).toEqual([]);
  });

  it('treats zero as unlimited', () => {
    const unlimited = plan({ quotas: { max_users: 0, max_companies: 0 } });
    expect(overQuotaLines(unlimited, { users: 900, companies: 90 }, words)).toEqual([]);
  });

  it('never warns about the plan the account is already on', () => {
    // Whatever today's plan holds, it holds — a warning there would ask
    // the customer to fix something that is not a decision.
    expect(overQuotaLines(plan({ current: true }), { users: 22, companies: 5 }, words))
      .toEqual([]);
  });

  it('names both when both are past', () => {
    expect(overQuotaLines(plan({}), { users: 80, companies: 5 }, words))
      .toEqual(['users 80/75', 'companies 5/3']);
  });
});
