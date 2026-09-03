import { describe, it, expect } from 'vitest';
import { initialsOf } from './initials';

describe('avatar initials', () => {
  it('first and last name', () => { expect(initialsOf('Allen Klein')).toBe('AK'); });
  it('one name → one letter', () => { expect(initialsOf('Allen')).toBe('A'); });
  it('three names → first and last', () => { expect(initialsOf('Ann Marie Lee')).toBe('AL'); });
  it('no name → the email\'s first letter', () => { expect(initialsOf('', 'adam@example.com')).toBe('A'); });
  it('nothing known → the product', () => { expect(initialsOf(null, null)).toBe('4'); });
  it('whitespace is not a name', () => { expect(initialsOf('   ', ' ')).toBe('4'); });
});
