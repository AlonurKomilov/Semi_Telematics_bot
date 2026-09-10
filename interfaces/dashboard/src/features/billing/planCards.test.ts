import { describe, expect, it } from 'vitest';
import { featureLines, money, plansIncluding, type CustomerPlan } from './planCards';

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
