/**
 * The page-sections gear — a user's personal arrangement of a
 * Pattern-B page (show/hide + reorder), persisted per user via the
 * preferences service (``page.<feature>.layout``, synced).
 *
 * Deliberate limits of this tier:
 *   - it offers only the sections in the page's BASE layout (persona
 *     default today; the manager's role default once that tier lands) —
 *     adding sections from OTHER personas is the manager tier's job,
 *     where availability can be vetted;
 *   - ``required`` sections are listed disabled with the reason, never
 *     omitted — a control that vanishes teaches nothing;
 *   - nothing here touches permissions.  Sections self-gate their data;
 *     this only arranges what the user could already see.
 */
import { useState } from 'react';
import { Popover as PopoverPrimitive } from '@base-ui/react/popover';
import { Settings2, Eye, EyeOff, ChevronUp, ChevronDown, RotateCcw } from 'lucide-react';
import { Tip } from '../../components/tooltip';
import { toneText } from '../../lib/status';
import { usePagePreference } from '../../preferences';
import { useViewPermissions } from '../../hooks/useViewPermissions';
import { usePermissions } from '../../hooks/usePermissions';
import { useShellConfig } from '../../hooks/useShellConfig';
import { useRoleView } from '../../context/RoleViewContext';
import { useRoleLayoutMutations } from './useRolePageLayouts';
import type { SectionRegistry } from './types';
import {
  canMove, gearItems, moveSection, resolvePageLayout, toggleSection,
} from './pageLayoutConfig';

interface PageSectionsGearProps<P extends object> {
  feature: string;
  registry: SectionRegistry<P>;
  /** The layout the user's arrangement is measured against — the team
   *  default when one exists, otherwise the shipped persona layout. */
  base: string[];
  /** True when ``base`` IS a stored team default (enables "clear"). */
  hasRoleDefault?: boolean;
}

export function PageSectionsGear<P extends object>({
  feature, registry, base, hasRoleDefault = false,
}: PageSectionsGearProps<P>) {
  const [open, setOpen] = useState(false);
  const { value: pref, setValue, resetValue } = usePagePreference(feature, 'layout');
  const items = gearItems(base, registry, pref);
  const customised = pref !== null;
  const adjustable = items.filter((i) => !i.required);

  // A page with nothing adjustable (HR's queue-only alerts view) gets no
  // gear at all — an empty menu is worse than none.
  if (adjustable.length === 0) return null;

  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      {/* The scope is in the label because a second gear — the config
          COG — now sits in the same corner on several pages. The icons
          differ (sliders vs cog); the words say which one changes only
          YOUR view and which changes the account for everyone. */}
      <Tip label="Page sections — your view only">
        <PopoverPrimitive.Trigger
          aria-label="Page sections — your view only"
          className={`inline-flex size-8 items-center justify-center rounded-md transition-colors ${
            open
              ? 'bg-primary/15 text-primary'
              : 'text-muted-foreground hover:bg-muted hover:text-foreground'
          }`}
        >
          <Settings2 size={16} aria-hidden />
        </PopoverPrimitive.Trigger>
      </Tip>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner align="end" sideOffset={6} className="z-50 outline-none">
          <PopoverPrimitive.Popup className="w-80 bg-popover text-popover-foreground border border-border rounded-md shadow-lg py-2 outline-none">
            <div className="px-3 pb-2 flex items-center justify-between border-b border-border">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Page sections
              </p>
              {customised && (
                <button
                  type="button"
                  onClick={() => resetValue()}
                  className="inline-flex items-center gap-1 text-2xs text-muted-foreground hover:text-foreground py-1 -my-1 min-h-tap"
                >
                  <RotateCcw size={12} aria-hidden /> Reset to default
                </button>
              )}
            </div>
            <ul className="py-1 max-h-80 overflow-y-auto">
              {items.map((item, idx) => (
                <li
                  key={item.id}
                  className="flex items-center gap-2 px-3 py-1.5 text-xs"
                >
                  {/* Show/hide.  Required rows state their reason instead
                      of offering a dead toggle. */}
                  {item.required ? (
                    <span className="flex-1 min-w-0 truncate text-muted-foreground">
                      {item.label}
                    </span>
                  ) : (
                    <button
                      type="button"
                      // Functional update: re-derive the item list from
                      // the FRESH stored value.  A hide followed quickly
                      // by a move both computed from this render's
                      // snapshot would silently revert the hide.
                      onClick={() => setValue((prev) =>
                        toggleSection(gearItems(base, registry, prev), item.id))}
                      aria-pressed={!item.hidden}
                      className={`flex-1 min-w-0 inline-flex items-center gap-2 text-left ${
                        item.hidden ? 'text-muted-foreground' : 'text-foreground'
                      }`}
                    >
                      {item.hidden
                        ? <EyeOff size={14} aria-hidden className="shrink-0" />
                        : <Eye size={14} aria-hidden className="shrink-0 text-primary" />}
                      <span className="truncate">{item.label}</span>
                    </button>
                  )}
                  {item.required ? (
                    <span className="text-2xs text-muted-foreground shrink-0">always shown</span>
                  ) : (
                    <span className="inline-flex shrink-0">
                      <button
                        type="button"
                        aria-label={`Move ${item.label} up`}
                        disabled={!canMove(items, idx, -1)}
                        onClick={() => setValue((prev) =>
                          moveSection(gearItems(base, registry, prev), item.id, -1))}
                        className="p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      >
                        <ChevronUp size={14} aria-hidden />
                      </button>
                      <button
                        type="button"
                        aria-label={`Move ${item.label} down`}
                        disabled={!canMove(items, idx, 1)}
                        onClick={() => setValue((prev) =>
                          moveSection(gearItems(base, registry, prev), item.id, 1))}
                        className="p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30"
                      >
                        <ChevronDown size={14} aria-hidden />
                      </button>
                    </span>
                  )}
                </li>
              ))}
            </ul>
            <p className="px-3 pt-1 text-2xs text-muted-foreground border-t border-border">
              The list above changes only your view — teammates keep theirs.
            </p>
            <ManagerBlock
              feature={feature}
              registry={registry}
              base={base}
              hasRoleDefault={hasRoleDefault}
            />
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  );
}


/**
 * The team-default door, inside the same gear.
 *
 * WHO sees it is the Permissions matrix's call, checked twice:
 *   - the ACTIVE VIEW's permission set must carry the grant, so a
 *     "view as Fleet" preview stays faithful — the block appears for
 *     exactly the people who'd see it on that view for real;
 *   - the CALLER must also be authorized (``can_manage_account``, or
 *     the grant on their own role's view) so the button is never a
 *     dead click — the server authorizes the caller, not the view.
 * "Use my current arrangement" captures the CURRENT resolved view —
 * what they see is what the team gets — and per Option A it remains a
 * default: teammates' own arrangements still apply on top.
 */
function ManagerBlock<P extends object>({
  feature, registry, base, hasRoleDefault,
}: {
  feature: string;
  registry: SectionRegistry<P>;
  base: string[];
  hasRoleDefault: boolean;
}) {
  const { has: viewHas } = useViewPermissions();
  const { has: iHave, role: myRole } = usePermissions();
  const { persona } = useShellConfig();
  const { viewLabel } = useRoleView();
  const { value: pref } = usePagePreference(feature, 'layout');
  const { save, clear, busy, lastAction } = useRoleLayoutMutations(persona, feature);

  // Drivers have no dashboard team defaults (the Mini App is their surface).
  const viewShows = viewHas('can_manage_account') || viewHas('can_manage_config_role');
  const callerMay = iHave('can_manage_account')
    || (iHave('can_manage_config_role') && myRole === persona);
  if (persona === 'driver' || !viewShows || !callerMay) return null;

  const current = resolvePageLayout(base, registry, pref);

  return (
    <div className="px-3 pt-2 mt-1 border-t border-border">
      <p className="text-2xs font-medium uppercase tracking-wide text-muted-foreground">
        Team default · {viewLabel}
      </p>
      <p className="text-2xs text-muted-foreground mt-0.5">
        Sets the starting layout for everyone on this role. They can
        still adjust their own view.
      </p>
      <div className="flex items-center gap-2 mt-1.5 pb-1">
        <button
          type="button"
          disabled={busy}
          onClick={() => save(current)}
          className="text-2xs font-medium text-primary hover:underline disabled:opacity-50 py-1 -my-1 min-h-tap"
        >
          Use my current arrangement
        </button>
        {hasRoleDefault && (
          <button
            type="button"
            disabled={busy}
            onClick={() => clear()}
            className="text-2xs text-muted-foreground hover:text-foreground disabled:opacity-50 py-1 -my-1 min-h-tap"
          >
            Clear team default
          </button>
        )}
      </div>
      {/* The clear-button appearing is too quiet to confirm "your whole
          team's page just changed" — say it.  Resets when the popover
          closes (the popup unmounts). */}
      {lastAction === 'saved' && (
        <p className={`text-2xs pb-1 ${toneText('ok')}`} role="status">
          Saved — the {viewLabel} team now starts from this layout.
        </p>
      )}
      {lastAction === 'cleared' && (
        <p className="text-2xs pb-1 text-muted-foreground" role="status">
          Team default cleared — back to the standard layout.
        </p>
      )}
    </div>
  );
}
