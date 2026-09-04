/**
 * Every mod control there is, composed for two surfaces.
 *
 * This file used to BE the panel — 1,029 lines holding seven option
 * arrays, two chip primitives, four category blocks and the whole
 * mod-install path. Every new axis edited it regardless of which
 * category the axis belonged to, and the category partition lived in
 * one string comparison, so a block dropped into the wrong `has()`
 * rendered under the wrong heading with the suite green.
 *
 * What is left here is the only thing that was ever shared: WHICH
 * groups appear WHERE. `compact` is the top-bar popover — what changes
 * often, plus a door to the rest; everything else is the page and the
 * profile card. `section` narrows the render to one category, which is
 * how the card asks for its sections and the /mods page asks for its
 * item level.
 *
 * The groups read their own state. `useMods` and `usePreference` are
 * subscriptions, and the profile card's `Section` already works this
 * way — a component takes what it uses rather than eleven props it
 * mostly ignores.
 */
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronRight, SlidersHorizontal } from 'lucide-react';
import { MODS_HREF } from '../href';
import type { ModSection } from '../taxonomy';
import { ModsRow, HAS_MODS } from './ModsRow';
import {
  ColorGroup, CornersGroup, MaterialGroup, TypefaceGroup, IconsGroup,
} from './Interface';
import { EffectsGroup } from './Effects';
import { SoundsGroup } from './Sounds';
import { SizeSlider } from './SizeSlider';

export function ModControls({ compact = false, onNavigate, section }: {
  compact?: boolean;
  /** Called when the door is taken, so the popover can close itself. */
  onNavigate?: () => void;
  /** Render ONE category. Undefined renders all of them, which is what
   *  the popover and the /mods item level both want. */
  section?: ModSection;
}) {
  const { t } = useTranslation();

  // The caps label above a group. The popover runs smaller — seven of
  // them stack inside `w-56` — and the page uses §4's canonical step.
  const groupLabel = compact
    ? 'text-2xs font-semibold uppercase tracking-wide text-muted-foreground'
    : 'text-xs font-medium uppercase tracking-wide text-muted-foreground';

  const has = (s: ModSection) => section === undefined || section === s;
  // A rule only when everything renders together; a single section is
  // already separated by the card it sits in.
  const rule = section === undefined
    ? <div className="border-t border-border" />
    : null;

  return (
    <>
      {/* The container's own row: installing a mod writes into every
          category below, which is why it is not one of them. */}
      {has('mods') && HAS_MODS && (
        <>
          <ModsRow label={groupLabel} />
          {rule}
        </>
      )}

      {/* Colour is the one Interface group the popover carries. */}
      {has('interface') && <ColorGroup label={groupLabel} />}

      {compact ? (
        <>
          <div className="border-t border-border" />
          <SizeSlider label={groupLabel} />

          {/* Corners, material, motion, sound and sizing BY REGION do
              not fit a w-56 popover, and they are settings rather than a
              quick toggle — design.md §7 forbids inventing an in-between
              width, so they live on the Mods page instead. */}
          <div className="border-t border-border" />
          {/* `min-h-tap` and the padding that earns it: this is the
              panel's only door to four of the seven axes, and as a bare
              `text-2xs` row its hit box was the line box — under the 24px
              WCAG 2.5.8 floor. `-my-1` gives the target back its height
              without spending any of the popover's vertical budget.
              The trailing chevron is what makes it read as a ROUTE
              rather than a caption under the controls. */}
          <Link
            to={MODS_HREF}
            onClick={onNavigate}
            className="flex items-center gap-1.5 text-2xs text-muted-foreground hover:text-foreground min-h-tap py-1 -my-1"
          >
            <SlidersHorizontal className="size-3 shrink-0" />
            {t('mods.all_customization', 'All customization…')}
            <ChevronRight className="size-3 shrink-0 ml-auto" />
          </Link>
        </>
      ) : (
        <>
          {rule}

          {has('interface') && (
            <>
              <CornersGroup label={groupLabel} />
              <MaterialGroup label={groupLabel} />
              <TypefaceGroup label={groupLabel} />
              <IconsGroup label={groupLabel} />
            </>
          )}

          {has('effects') && <EffectsGroup label={groupLabel} />}

          {rule}

          {has('sounds') && <SoundsGroup label={groupLabel} />}
        </>
      )}
    </>
  );
}
