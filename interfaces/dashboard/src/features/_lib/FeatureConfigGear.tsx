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
import { useTranslation } from 'react-i18next';
import { Settings2 } from 'lucide-react';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Tip } from '../../components/tooltip';
import { useRoleView } from '../../context/RoleViewContext';

interface FeatureConfigGearProps {
  /** Feature name, used in the dialog title: "KPI settings". */
  feature: string;
  /** The feature's config UI. Rendered only while the dialog is open, so
   *  a panel that fetches on mount does not fetch on every page load. */
  children: ReactNode;
  /** Widen for config with more than a short form. */
  size?: 'lg' | 'xl' | '2xl';
}

export function FeatureConfigGear({
  feature, children, size = 'lg',
}: FeatureConfigGearProps) {
  const { t } = useTranslation();
  const { viewHas } = useRoleView();
  const [open, setOpen] = useState(false);

  // Account-wide config. Not the feature's own Manage — View / Manage /
  // Config are three actions (docs/architecture/config.md).
  if (!viewHas('can_manage_config_all')) return null;

  // "KPI config", not "KPI settings" — and not the name of whatever the
  // config happens to contain ("Thresholds", "DQF export", "When sources
  // disagree"). One word, every feature, so the label teaches where the
  // next feature keeps its config instead of naming this one's payload.
  // What is INSIDE keeps its own name: DQF is a thing in Applications
  // config, not a config of its own.
  const label = t('config.gear_label', '{{feature}} config', { feature });

  return (
    <>
      <Tip label={label}>
        <button
          type="button"
          aria-label={label}
          onClick={() => setOpen(true)}
          className="inline-flex items-center justify-center size-8 rounded-md border border-border text-muted-foreground hover:bg-muted hover:text-foreground transition"
        >
          <Settings2 size={16} />
        </button>
      </Tip>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className={
          size === '2xl' ? 'max-w-2xl' : size === 'xl' ? 'max-w-xl' : 'max-w-lg'
        }>
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
