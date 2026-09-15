/**
 * The map draws what the control says it draws.
 *
 * Three places decide the opening view and they have to agree: the
 * STATE the control renders from, the REFS the map reads, and the tile
 * config the FIRST PAINT builds from.  They did not.  `initCfg` named
 * `TILES['standard']` outright and `applyLabels` ran only on a later
 * swap, so moving the default would have drawn standard tiles, unnamed,
 * under a button reading "Terrain".
 *
 * A control that describes a map it is not showing is the same fault
 * this codebase keeps finding in other clothes, so the agreement is
 * pinned rather than left to whoever edits one of the three next.
 */
import { describe, it, expect } from 'vitest';
import src from './useLeafletMap.ts?raw';

const code = (src as unknown as string)
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .split('\n').map((l) => l.replace(/\s\/\/.*$/, '')).join('\n');

/** What the state, the refs and the first paint each open on. */
function opens() {
  return {
    stateType: /useState<MapType>\('(\w+)'\)/.exec(code)?.[1],
    stateLabels: /const \[showLabels, setShowLabelsState\] = useState\((\w+)\)/.exec(code)?.[1],
    refType: /useRef<MapType>\('(\w+)'\)/.exec(code)?.[1],
    refLabels: /const showLabelsRef = useRef\((\w+)\)/.exec(code)?.[1],
  };
}

describe('the opening view', () => {
  it('is terrain with labels, which is what the owner asked for', () => {
    const o = opens();
    expect(o.stateType, 'the map type the control renders').toBe('terrain');
    expect(o.stateLabels, 'labels on').toBe('true');
  });

  it('agrees between the state the control reads and the refs the map reads', () => {
    const o = opens();
    expect(o.refType, 'the ref the tile layer is built from').toBe(o.stateType);
    expect(o.refLabels, 'the ref applyLabels reads').toBe(o.stateLabels);
  });

  it('builds the first tile layer from the ref, not from a named type', () => {
    expect(code, 'the first paint hardcodes a map type again — the control '
      + 'will say one thing and the map will draw another')
      .not.toMatch(/const initCfg = TILES\['\w+'\]/);
    expect(code).toMatch(/const initCfg = TILES\[mapTypeRef\.current\]/);
  });

  it('puts the names on at the first paint, not only at the first swap', () => {
    const build = code.slice(code.indexOf('const initCfg'));
    const upToReady = build.slice(0, build.indexOf('setIsReady'));
    expect(upToReady, 'applyLabels is not called while the map is being '
      + 'built, so a labelled default comes up bare until something else '
      + 'triggers a swap').toMatch(/applyLabels\(map, mapTypeRef\.current\)/);
  });
});
