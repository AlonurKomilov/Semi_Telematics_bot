/**
 * The top-bar popover — the trigger, the box, and how it closes.
 *
 * Shell only. Everything inside it is `ModControls compact`, which is
 * the same component the profile card and the /mods page render.
 */
import { useState, useRef, useEffect, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { Palette } from 'lucide-react';
import { Button } from '../../components/ui/button';
import { useMods } from '../context';
import { ModControls } from './ModControls';

/**
 * The top-bar entry point: a button, a popover, and the compact controls.
 * It owns only the open/closed question — every control inside it belongs
 * to `ModControls`, which the /mods page renders in full.
 */
export function ModPanel() {
  const { t } = useTranslation();
  const { size } = useMods();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) { if (e.key === 'Escape') setOpen(false); }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      {/* `mods.picker`, not the pre-existing `theme.toggle` ("Toggle
          theme") — this opens a menu of three settings, it does not
          flip one. */}
      <Button
        variant="ghost"
        size="icon"
        className="shrink-0"
        aria-label={t('mods.picker', 'Mods')}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <Palette />
      </Button>

      {open && (
        <div
          // The picker holds its OWN size, like the /profile panel: it
          // lives in the `controls` region AND drives the global, so
          // without this the slider grows and slides under the pointer
          // mid-drag. A browser audit measured the same runaway here as
          // on /profile. The page behind the popover still previews.
          style={{
            '--size-region': 1,
            '--size-text': size.text * size.global,
            '--size-control': size.control * size.global,
            '--size-layout': size.layout * size.global,
            '--size-panel': size.panel * size.global,
          } as CSSProperties}
          className="absolute right-0 top-10 z-50 w-56 bg-popover border border-border rounded-xl shadow-xl p-3 space-y-3"
        >
          <ModControls compact onNavigate={() => setOpen(false)} />
        </div>
      )}
    </div>
  );
}
