import { useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  ClipboardCheck, ChevronUp, ChevronDown, Trash2, RotateCcw, Plus, X,
  Image as ImageIcon, Camera, FileText, CheckSquare,
} from 'lucide-react';
import { apiJSON } from '../../api/client';
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from '../../components/shell';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';
import type { PTITemplate, PTITemplateItem } from '../../types';
import { Dialog, DialogContent } from '../../components/ui/dialog';

/**
 * Fleet-side template editor for PTI checklists.
 *
 * Two tabs (Truck · Trailer) — each shows the active template's items
 * in display order with up/down arrows for reorder, toggle switches
 * for Required + Photo required, and a delete button.
 *
 * Editing flow:
 *  - Reorder is local-optimistic; on each press we POST the new
 *    sequence and the server bumps ``sort_order`` for every row.
 *  - Toggle/edit/add hits PUT /api/inspections/template/{type}/items/{key}
 *    immediately (no draft state — each row is its own atomic save).
 *  - Reset wipes the template, bumps the version, and seeds from
 *    Standard DOT — confirmed via window.confirm because it loses
 *    every custom item the fleet added.
 *
 * Snapshot-on-spawn means edits here don't affect inspections that
 * are already in-flight — only the NEXT spawn picks up the new
 * checklist.  Drivers in the middle of a PTI keep working against the
 * template version that was active when their inspection was created.
 *
 * @dnd-kit isn't pulled in for this page — up/down arrow buttons are
 * adequate for a ~20-item list and skip the bundle cost.
 */


type VehicleType = 'truck' | 'trailer';


function _formatDate(iso: string | null | undefined, tz?: string): string {
  return formatDate(iso, { timeZone: tz, intl: { dateStyle: 'medium', timeStyle: 'short' } });
}


// ── Add-item modal ─────────────────────────────────────────────────


function AddItemDialog({
  vehicleType,
  onAdded,
  onClose,
}: {
  vehicleType: VehicleType;
  onAdded: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [itemKey, setItemKey] = useState('');
  const [label, setLabel] = useState('');
  const [category, setCategory] = useState('');
  const [requiresMedia, setRequiresMedia] = useState(false);
  const [required, setRequired] = useState(true);
  const [itemType, setItemType] = useState<'check' | 'photo' | 'document'>('check');
  const [saving, setSaving] = useState(false);

  const itemTypeItems = [
    { value: 'check', label: t('inspections.item_type_check') },
    { value: 'photo', label: t('inspections.item_type_photo') },
    { value: 'document', label: t('inspections.item_type_document') },
  ];

  // Auto-generate a key from the label so the fleet doesn't have to
  // think about identifier syntax.  Editable if the auto-key clashes
  // with an existing one.
  const autoKey = useMemo(() => {
    return label
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 40);
  }, [label]);
  const finalKey = (itemKey || autoKey).trim();

  const save = async () => {
    if (!label.trim() || !finalKey) return;
    setSaving(true);
    try {
      await apiJSON(`/inspections/template/${vehicleType}/items/${encodeURIComponent(finalKey)}`, {
        method: 'PUT',
        body: {
          label: label.trim(),
          category: category.trim(),
          requires_media: requiresMedia,
          required,
          item_type: itemType,
          sort_order: 9999,  // appended; reorder bumps it into place
        },
      });
      toast.success(t('inspections.item_added'));
      onAdded();
      onClose();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.item_add_failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    // Was a hand-rolled backdrop + panel: a click-away and nothing else,
    // so no focus trap, no Escape, no ``aria-modal`` and no background
    // scroll lock.  <Dialog> brings all four AND the scrolling — its
    // popup is already capped at the viewport with ``overflow-y-auto``
    // and (since this pass) ``overscroll-contain``, so the modal no
    // longer scrolls the page behind it.
    <Dialog open onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent showCloseButton={false} size="lg" className="p-5">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold">
            {t('inspections.add_item_title', { vehicleType })}
          </h3>
          <button onClick={onClose} aria-label="Close" className="inline-flex items-center justify-center min-w-tap text-muted-foreground hover:text-foreground py-0.5 -my-0.5 min-h-tap">
            <X className="size-4" />
          </button>
        </div>

        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">{t('inspections.label_field')}</span>
          <input
            value={label}
            onChange={e => setLabel(e.target.value)}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
            placeholder="e.g. Air filter check"
            maxLength={120}
            autoFocus
          />
        </label>

        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">
            {t('inspections.key_field')}
            <span className="ml-1 text-muted-foreground/70">({t('inspections.key_hint')})</span>
          </span>
          <input
            value={itemKey || autoKey}
            onChange={e => setItemKey(e.target.value)}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm font-mono"
            placeholder="air_filter"
            maxLength={40}
          />
        </label>

        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">{t('inspections.category_field')}</span>
          <input
            value={category}
            onChange={e => setCategory(e.target.value)}
            className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm"
            placeholder="engine, brakes, tires, …"
            maxLength={40}
          />
        </label>

        <label className="block text-xs">
          <span className="block text-muted-foreground mb-1">{t('inspections.item_type_field')}</span>
          <Select
            value={itemType}
            onValueChange={(v) => setItemType(v as 'check' | 'photo' | 'document')}
            items={itemTypeItems}
          >
            <SelectTrigger className="w-full" aria-label={t('inspections.item_type_field')}><SelectValue /></SelectTrigger>
            <SelectContent>
              {itemTypeItems.map(it => (
                <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>

        {/* Required applies to all types; Photo-required only matters
            for a 'check' item (photo/document items always need their
            capture, enforced server-side). */}
        <div className="flex items-center gap-4 text-sm">
          <label className="inline-flex items-center gap-1.5">
            <input
              type="checkbox"
              checked={required}
              onChange={e => setRequired(e.target.checked)}
              className="accent-primary"
            />
            <span>{t('inspections.required_field')}</span>
          </label>
          {itemType === 'check' && (
            <label className="inline-flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={requiresMedia}
                onChange={e => setRequiresMedia(e.target.checked)}
                className="accent-primary"
              />
              <span>{t('inspections.photo_required_field')}</span>
            </label>
          )}
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-sm rounded-md border border-border hover:bg-muted min-h-tap"
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving || !label.trim() || !finalKey}
            className="px-3 py-1.5 text-sm rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:opacity-50 min-h-tap"
          >
            {saving ? t('common.saving') : t('inspections.add_item_btn')}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}


// ── Item row ───────────────────────────────────────────────────────


function ItemRow({
  item,
  vehicleType,
  isFirst,
  isLast,
  onMoveUp,
  onMoveDown,
  onChange,
}: {
  item: PTITemplateItem;
  vehicleType: VehicleType;
  isFirst: boolean;
  isLast: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onChange: () => void;
}) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);
  const itemType = (item.item_type ?? 'check') as 'check' | 'photo' | 'document';

  const upsert = async (patch: { required?: boolean; requires_media?: boolean }) => {
    setSaving(true);
    try {
      await apiJSON(`/inspections/template/${vehicleType}/items/${encodeURIComponent(item.item_key)}`, {
        method: 'PUT',
        body: {
          label:          item.label,
          category:       item.category,
          requires_media: patch.requires_media ?? (item.requires_media === 1),
          required:       patch.required       ?? (item.required === 1),
          item_type:      itemType,   // preserve — don't reset to 'check'
          sort_order:     item.sort_order,
        },
      });
      onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.item_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  const uploadReference = async (file: File) => {
    setSaving(true);
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      await apiJSON(
        `/inspections/template/${vehicleType}/items/${encodeURIComponent(item.item_key)}/reference-image`,
        { method: 'POST', body: fd },
      );
      toast.success(t('inspections.ref_image_saved'));
      onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.ref_image_failed'));
    } finally {
      setSaving(false);
    }
  };

  const clearReference = async () => {
    setSaving(true);
    try {
      await apiJSON(
        `/inspections/template/${vehicleType}/items/${encodeURIComponent(item.item_key)}/reference-image`,
        { method: 'DELETE' },
      );
      onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.ref_image_failed'));
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    const ok = window.confirm(
      t('inspections.delete_confirm', { label: item.label }),
    );
    if (!ok) return;
    setSaving(true);
    try {
      await apiJSON(`/inspections/template/${vehicleType}/items/${encodeURIComponent(item.item_key)}`, {
        method: 'DELETE',
      });
      onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.item_delete_failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={`flex items-center gap-2 px-3 py-2 border border-border rounded-md ${saving ? 'opacity-60' : ''}`}>
      <div className="flex flex-col gap-0.5 flex-shrink-0">
        <button
          type="button"
          aria-label="Move up"
          disabled={isFirst || saving}
          onClick={onMoveUp}
          className="inline-flex items-center justify-center min-w-tap p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30 min-h-tap"
        >
          <ChevronUp className="size-3.5" />
        </button>
        <button
          type="button"
          aria-label="Move down"
          disabled={isLast || saving}
          onClick={onMoveDown}
          className="inline-flex items-center justify-center min-w-tap p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-30 min-h-tap"
        >
          <ChevronDown className="size-3.5" />
        </button>
      </div>

      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium truncate flex items-center gap-1.5">
          {itemType === 'photo' && <Camera className="text-chart-1 flex-shrink-0 size-3.5" />}
          {itemType === 'document' && <FileText className="text-chart-1 flex-shrink-0 size-3.5" />}
          {itemType === 'check' && <CheckSquare className="text-muted-foreground flex-shrink-0 size-3.5" />}
          <span className="truncate">{item.label}</span>
        </div>
        <div className="text-xs text-muted-foreground flex items-center gap-2">
          <span className="font-mono">{item.item_key}</span>
          {item.category && <span>· {item.category}</span>}
        </div>
      </div>

      {/* Reference photo control — upload/replace/clear the example
          shot the driver sees in the wizard. */}
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={e => {
          const f = e.target.files?.[0];
          e.target.value = '';
          if (f) uploadReference(f);
        }}
      />
      {item.reference_image_url ? (
        <div className="flex items-center gap-1 flex-shrink-0">
          <img
            src={`/api/inspections/template-ref/${item.reference_image_url}`}
            alt=""
            className="w-9 h-9 rounded object-cover border border-border cursor-pointer"
            title={t('inspections.ref_image_replace')}
            onClick={() => fileRef.current?.click()}
          />
          <button
            type="button"
            onClick={clearReference}
            disabled={saving}
            title={t('inspections.ref_image_clear')}
            className="inline-flex items-center justify-center min-w-tap p-1 text-muted-foreground hover:text-destructive min-h-tap"
          >
            <X className="size-3" />
          </button>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={saving}
          title={t('inspections.ref_image_add')}
          className="flex-shrink-0 inline-flex items-center gap-1 px-2 py-1 text-2xs border border-border rounded-md hover:bg-muted text-muted-foreground min-h-tap"
        >
          <ImageIcon className="size-3" />
          {t('inspections.ref_image_add')}
        </button>
      )}

      <label className="inline-flex items-center gap-1 text-xs whitespace-nowrap">
        <input
          type="checkbox"
          checked={item.required === 1}
          onChange={e => upsert({ required: e.target.checked })}
          disabled={saving}
          className="accent-primary"
        />
        <span>{t('inspections.required_field')}</span>
      </label>

      {itemType === 'check' && (
        <label className="inline-flex items-center gap-1 text-xs whitespace-nowrap">
          <input
            type="checkbox"
            checked={item.requires_media === 1}
            onChange={e => upsert({ requires_media: e.target.checked })}
            disabled={saving}
            className="accent-primary"
          />
          <span>{t('inspections.photo_required_field')}</span>
        </label>
      )}

      <button
        type="button"
        onClick={remove}
        disabled={saving}
        title={t('common.delete')}
        className="flex-shrink-0 p-1.5 text-muted-foreground hover:text-destructive hover:bg-destructive/10 rounded min-h-tap"
      >
        <Trash2 className="size-3.5" />
      </button>
    </div>
  );
}


// ── Template card (one per vehicle type) ───────────────────────────


function TemplateCard({
  template,
  vehicleType,
  onChange,
}: {
  template: PTITemplate | null;
  vehicleType: VehicleType;
  onChange: () => void;
}) {
  const { t } = useTranslation();
  const tz = useTimezone();
  const [adding, setAdding] = useState(false);

  const items = template?.items ?? [];

  const move = async (idx: number, dir: -1 | 1) => {
    const next = idx + dir;
    if (next < 0 || next >= items.length) return;
    const reordered = items.slice();
    [reordered[idx], reordered[next]] = [reordered[next], reordered[idx]];
    try {
      await apiJSON(`/inspections/template/reorder`, {
        method: 'POST',
        body: {
          vehicle_type: vehicleType,
          ordered_keys: reordered.map(i => i.item_key),
        },
      });
      onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.reorder_failed'));
    }
  };

  const reset = async () => {
    const ok = window.confirm(
      t('inspections.reset_confirm', { vehicleType }),
    );
    if (!ok) return;
    try {
      await apiJSON(`/inspections/template/reset`, {
        method: 'POST',
        body: { vehicle_type: vehicleType },
      });
      toast.success(t('inspections.reset_done'));
      onChange();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.reset_failed'));
    }
  };

  if (!template) {
    return (
      <EmptyState
        icon={ClipboardCheck}
        title={t('inspections.no_template')}
        description={t('inspections.no_template_desc')}
      />
    );
  }

  return (
    <>
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs text-muted-foreground">
          {t('inspections.template_meta', {
            version: template.version,
            updated: _formatDate(template.updated_at, tz),
            count: items.length,
          })}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium border border-border rounded-md hover:bg-muted min-h-tap"
          >
            <Plus className="size-3" />
            {t('inspections.add_item_btn')}
          </button>
          <button
            type="button"
            onClick={reset}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium border border-destructive/30 text-destructive rounded-md hover:bg-destructive/10 min-h-tap"
          >
            <RotateCcw className="size-3" />
            {t('inspections.reset_to_default')}
          </button>
        </div>
      </div>

      <div className="space-y-2">
        {items.map((item, idx) => (
          <ItemRow
            key={item.item_key}
            item={item}
            vehicleType={vehicleType}
            isFirst={idx === 0}
            isLast={idx === items.length - 1}
            onMoveUp={() => move(idx, -1)}
            onMoveDown={() => move(idx, 1)}
            onChange={onChange}
          />
        ))}
      </div>

      {adding && (
        <AddItemDialog
          vehicleType={vehicleType}
          onAdded={onChange}
          onClose={() => setAdding(false)}
        />
      )}
    </>
  );
}


// ── Page ───────────────────────────────────────────────────────────


export default function TemplateEditor() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [tab, setTab] = useState<VehicleType>('truck');

  const { data, isLoading, error } = useQuery({
    queryKey: ['pti-template'],
    queryFn: () => apiJSON<{ truck: PTITemplate | null; trailer: PTITemplate | null }>('/inspections/template'),
  });

  const refresh = () => qc.invalidateQueries({ queryKey: ['pti-template'] });

  return (
    <div>
      <p className="text-sm text-muted-foreground mb-3">
        {t('pages.template_editor_desc')}
      </p>

      <div className="inline-flex gap-1 mb-4">
        {(['truck', 'trailer'] as VehicleType[]).map(v => (
          <button
            key={v}
            type="button"
            onClick={() => setTab(v)}
            className={`px-3 py-1.5 text-sm rounded-md ${tab === v ? 'bg-primary text-primary-foreground' : 'hover:bg-muted border border-border'}`}
          >
            {t(`inspections.tab_${v}`)}
          </button>
        ))}
      </div>

      {error && (
        <div className="mb-3">
          <ErrorState message={error instanceof Error ? error.message : 'Failed to load template'} />
        </div>
      )}

      {isLoading && !data ? (
        <TableSkeleton rows={6} cols={1} />
      ) : (
        <TemplateCard
          template={tab === 'truck' ? data?.truck ?? null : data?.trailer ?? null}
          vehicleType={tab}
          onChange={refresh}
        />
      )}
    </div>
  );
}
