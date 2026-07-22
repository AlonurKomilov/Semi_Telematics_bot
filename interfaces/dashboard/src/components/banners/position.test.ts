import { describe, it, expect, beforeEach } from 'vitest';
import {
  getNotifPosition, resetNotifPositionForTests, setNotifPosition,
} from './position';

beforeEach(() => {
  localStorage.clear();
  resetNotifPositionForTests();
});

describe('notification position store', () => {
  it('defaults to top-right', () => {
    expect(getNotifPosition()).toBe('top-right');
  });

  it('roundtrips a chosen position', () => {
    setNotifPosition('bottom-center');
    expect(getNotifPosition()).toBe('bottom-center');
  });

  it('falls back to the default on a corrupted stored value', () => {
    localStorage.setItem('notif.position', 'under-the-couch');
    expect(getNotifPosition()).toBe('top-right');
  });
});
