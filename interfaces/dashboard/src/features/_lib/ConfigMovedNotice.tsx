/**
 * "Your settings moved to the gear" — a one-release pointer.
 *
 * Config used to be a labelled control in each page's body: a
 * "Thresholds" button on KPI, "DQF export" on Applications, a "When
 * sources disagree" panel on Integrations, a backend card on Storage, a
 * card headed "Configuration" on Settings. All of it now lives behind an
 * unlabelled cog in the page header.
 *
 * That is a better long-term shape and a worse day-one experience: a user
 * who knew where a thing was finds it gone, with no breadcrumb. An icon
 * cannot be searched for and cannot be guessed by someone who was not
 * looking for a gear. So the page says where it went, once.
 *
 * DELETE THIS AFTER THE RELEASE. It exists to carry people across one
 * move, not to permanently annotate the header. Remove the component, its
 * call sites, and the `config.moved_notice_dismissed` preference
 * together — a notice nobody can dismiss twice is just clutter with a
 * close button.
 */
import { Cog, X } from 'lucide-react';
import { usePreference } from '../../preferences';

export function ConfigMovedNotice({ what }: { what: string }) {
  const { value: dismissed, setValue: setDismissed } = usePreference(
    'config.moved_notice_dismissed',
  );
  if (dismissed) return null;

  return (
    <div className="mb-3 flex items-start gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2">
      <Cog size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
      <p className="flex-1 text-xs text-muted-foreground">
        <span className="text-foreground font-medium">{what}</span> moved to
        the gear in the page header, with every other feature&rsquo;s
        configuration.
      </p>
      <button
        type="button"
        onClick={() => setDismissed(true)}
        aria-label="Dismiss"
        className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition"
      >
        <X size={14} />
      </button>
    </div>
  );
}

export default ConfigMovedNotice;
