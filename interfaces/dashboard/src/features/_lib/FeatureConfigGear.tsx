/**
 * The feature config gear — ONE entry point for a feature's settings.
 *
 * Config used to be wherever each feature happened to put it: KPI had a
 * "Thresholds" button with a sliders icon, Applications a dialog on the
 * header, Integrations a panel halfway down the page, Storage a card,
 * General settings a section called "Configuration". Six surfaces, six
 * shapes, six icons — and no way to learn "where do I change this?" once
 * and have it hold on the next page.
 *
 * So: a gear in the PageHeader, in the same place on every feature, and
 * whatever that feature can be configured to do lives INSIDE it. DQF is
 * not a thing next to Applications' config; it is a thing in it.
 *
 * THE ICON IS A COG, and that is load-bearing. `PageSectionsGear` uses
 * `Settings2` — the sliders glyph — and this used the same one, so the
 * two gears were pixel-identical while meaning different things: one
 * rearranges what YOU see, the other changes an account-wide value for
 * everyone. Sliders read as "adjust my view"; a cog reads as "configure
 * the thing". Keep them different.
 *
 * RELATIONSHIP TO THE OTHER GEAR. `PageSectionsGear` is a different tier
 * and deliberately stays separate: it arranges the sections of a
 * Pattern-B page (show/hide/reorder), touches no permissions, and is a
 * per-user preference. This one edits ACCOUNT-WIDE VALUES behind
 * `can_manage_config_all` — changing something here changes it for
 * everyone. A page can carry both; they answer different questions
 * ("what do I see?" vs "what does this feature do?").
 *
 * GATING. The gear renders nothing at all without the config permission,
 * rather than rendering disabled. A disabled gear on every page would
 * advertise a door most users can never open. Where a feature wants to
 * explain the absence, it does so in its own copy — as the storage
 * backend chooser and forum routing table already do.
 */
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Cog } from 'lucide-react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Tip } from '../../components/tooltip';
import { useRoleView } from '../../context/RoleViewContext';

interface FeatureConfigGearProps {
  /** Feature name, used in the dialog title: "KPI configuration". */
  feature: string;
  /** The feature's config UI. Rendered only while the dialog is open, so
   *  a panel that fetches on mount does not fetch on every page load.
   *  Omit when passing `to`. */
  children?: ReactNode;
  /** Widen for config with more than a short form. */
  size?: 'lg' | 'xl' | '2xl';
  /** Config that is a PAGE, not a panel — the gear navigates instead of
   *  opening a dialog. Scorecards' rules are a full CRUD editor and
   *  Alerts' live inside Group delivery; neither shrinks into a dialog
   *  honestly. Same icon, same slot, same permission — only the
   *  destination differs, which is the point: a user learns one place to
   *  look and it holds even where the config is too big for a popup. */
  to?: string;
}

export function FeatureConfigGear({
  feature, children, size = 'lg', to,
}: FeatureConfigGearProps) {
  const { t } = useTranslation();
  const { viewHas } = useRoleView();
  const [open, setOpen] = useState(false);

  // Account-wide config. Not the feature's own Manage — View / Manage /
  // Config are three actions (docs/architecture/config.md).
  if (!viewHas('can_manage_config_all')) return null;

  // "KPI configuration" — the whole word.
  //
  // Three names were tried and two were wrong. "KPI settings" collided
  // with Settings, the feature. "KPI config" fixed that but reads as
  // developer shorthand: `config` is what a file is called, not what an
  // owner calls a screen. "Configuration" is the plain English noun, it
  // matches the permission column the owner already sees (Config ·
  // account-wide), and it survives translation.
  //
  // Still never named after the payload — not "Thresholds", "DQF export"
  // or "When sources disagree". The label teaches where the NEXT feature
  // keeps its configuration; what is inside keeps its own name.
  const label = t('config.gear_label', '{{feature}} configuration', { feature });

  const gearCls =
    'inline-flex items-center justify-center size-8 rounded-md border '
    + 'border-border text-muted-foreground hover:bg-muted hover:text-foreground transition';

  // Page-mode: same gear, same slot, same gate — it just navigates.
  if (to) {
    return (
      <Tip label={label}>
        <Link to={to} aria-label={label} className={gearCls}>
          <Cog size={16} />
        </Link>
      </Tip>
    );
  }

  return (
    <>
      <Tip label={label}>
        <button
          type="button"
          aria-label={label}
          onClick={() => setOpen(true)}
          className={gearCls}
        >
          <Cog size={16} />
        </button>
      </Tip>
      <Dialog open={open} onOpenChange={setOpen}>
        {/* Tall dialogs anchor to the TOP; only `lg` stays centred.
            DialogContent is `top-1/2 … -translate-y-1/2`, so a centred box
            grows in BOTH directions — expanding a disclosure inside it
            moves the summary the user just clicked upward, out from under
            the cursor.  Anchoring makes growth push downward only. */}
        <DialogContent
          className={
            size === '2xl' ? 'max-w-2xl top-[8vh] translate-y-0'
              : size === 'xl' ? 'max-w-xl top-[8vh] translate-y-0'
                : 'max-w-lg'
          }
        >
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
          </DialogHeader>
          {/* Mounted only while open — see `children` above. */}
          {open && children}
        </DialogContent>
      </Dialog>
    </>
  );
}

export default FeatureConfigGear;
