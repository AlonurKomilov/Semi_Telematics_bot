/**
 * The top-bar popover — the trigger, the box, and how it closes.
 *
 * Shell only. Everything inside it is `ModControls compact`, which is
 * the same component the profile card and the /mods page render.
 */
import { useState, useRef, useLayoutEffect, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Palette } from '../../lib/icons';
import { Button } from '../../components/ui/button';
import { useMods } from '../context';
import { ModControls } from './ModControls';
import { Dropdown } from '../../components/ui/context-menu';

/**
 * The top-bar entry point: a button, a popover, and the compact controls.
 * It owns only the open/closed question — every control inside it belongs
 * to `ModControls`, which the /mods page renders in full.
 */
export function ModPanel() {
  const { t } = useTranslation();
  const { size } = useMods();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  /**
   * How much room this popover actually has, measured rather than
   * guessed.
   *
   * `calc(100vh - 4rem)` would encode today's header and gutter, and
   * both move with the Size axis — `chrome.test.ts` refuses a viewport
   * calc that subtracts the shell frame for exactly that reason. The
   * panel's own top edge is the honest input, so it is read from the
   * element after it opens and again on a resize.
   */
  const [maxH, setMaxH] = useState<number | undefined>(undefined);
  useLayoutEffect(() => {
    if (!open) return;
    const fit = () => {
      const el = panelRef.current;
      if (el) setMaxH(Math.max(160, window.innerHeight - el.getBoundingClientRect().top - 16));
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [open]);
  // Click-outside and Escape were two `document` listeners here, and
  // are the primitive's now — along with the thing they could not give:
  // this panel is PORTALLED. It was an `absolute` box inside the
  // topbar, and while it sat there the topbar could never be given a
  // `backdrop-filter`: an ancestor carrying one becomes a backdrop
  // root, so this panel's own filter would resolve against nothing and
  // it would show the page straight through itself.

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      className="w-56"
      trigger={(
        /* `mods.picker`, not the pre-existing `theme.toggle` ("Toggle
           theme") — this opens a menu of three settings, it does not
           flip one.
           No `onClick` and no `aria-expanded` of its own: the trigger
           owns both, and a second toggle would open the panel and close
           it again in the same click. */
        <Button
          variant="ghost"
          size="icon"
          className="shrink-0"
          aria-label={t('mods.picker', 'Mods')}
        >
          <Palette />
        </Button>
      )}
    >
      <div
          // The picker holds its OWN size, like the /profile panel: it
          // lives in the `controls` region AND drives the global, so
          // without this the slider grows and slides under the pointer
          // mid-drag. A browser audit measured the same runaway here as
          // on /profile. The page behind the popover still previews.
          ref={panelRef}
          style={{
            maxHeight: maxH,
            '--size-region': 1,
            '--size-text': size.text * size.global,
            '--size-control': size.control * size.global,
            '--size-layout': size.layout * size.global,
            '--size-panel': size.panel * size.global,
          } as CSSProperties}
          // Scrollable, capped by MEASUREMENT — see `maxH` above. It is a
          // column of groups that grows every time an axis is added, and
          // at 706px it already clipped a 1366x768 laptop, taking the
          // Size slider and the door to the rest of the axes with it.
          // `overscroll-contain` keeps a scroll inside it from moving
          // whatever is behind.
          // The chrome — surface, border, radius, shadow — belongs to
          // the primitive now. What stays here is what this panel alone
          // needs: its measured cap, its own size region, and a scroller.
          className="overflow-y-auto overscroll-contain p-3 space-y-3"
        >
          <ModControls compact onNavigate={() => setOpen(false)} />
      </div>
    </Dropdown>
  );
}
