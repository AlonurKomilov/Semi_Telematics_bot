import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { PillarRing } from './PillarRing';

describe('<PillarRing />', () => {
  it('renders the composite total in the centre', () => {
    const { getByText, getByRole } = render(
      <PillarRing
        safety={{ subtotal: 30, cap: 40, has_data: true }}
        efficiency={{ subtotal: 25, cap: 35, has_data: true }}
        compliance={{ subtotal: 20, cap: 25, has_data: true }}
        total={75}
      />,
    );

    expect(getByText('75')).toBeTruthy();
    const svg = getByRole('img');
    expect(svg.getAttribute('aria-label')).toBe(
      'Composite score 75 out of 100',
    );
  });

  it('renders with no-data pillars without crashing', () => {
    const { getByText } = render(
      <PillarRing
        safety={{ subtotal: 0, cap: 40, has_data: false }}
        efficiency={{ subtotal: 0, cap: 35, has_data: false }}
        compliance={{ subtotal: 0, cap: 25, has_data: false }}
        total={0}
      />,
    );
    expect(getByText('0')).toBeTruthy();
  });
});
