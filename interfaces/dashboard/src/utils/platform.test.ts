import { describe, it, expect } from 'vitest';
import { shortcut, modKey, isMac } from './platform';

describe('platform shortcut display', () => {
  it('formats the mod chord for the current OS', () => {
    // jsdom is not Mac → Ctrl with a separator.
    expect(isMac).toBe(false);
    expect(modKey).toBe('Ctrl');
    expect(shortcut('K')).toBe('Ctrl+K');
    expect(shortcut('J')).toBe('Ctrl+J');
  });
});
