import { useState, useEffect, useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import {
  FileText, Save, ArrowLeft, Trash2, Plus, Paperclip,
  Receipt, X, Link as LinkIcon, Image as ImageIcon,
} from 'lucide-react';
import { apiJSON, apiFetch } from '../../api/client';
import { PageHeader, ErrorState } from '../../components/shell';
import { toneClasses } from '../../lib/status';
import type {
  WorkOrder, WorkOrderDetail, WorkOrderPart, WorkOrderAttachment,
  MaintenanceTask,
} from '../../types';

// ── Empty-state factories ────────────────────────────────────────
//
// New work orders start with the bare minimum: vehicle, status=draft,
// everything else zero/blank.  The cost totals auto-update from parts
// + labor + tax as the user types.

const blankWorkOrder = (): Partial<WorkOrder> => ({
  vehicle_name: '',
  vehicle_type: '',
  company_code: '',
  vendor_name: '',
  vendor_address: '',
  vendor_phone: '',
  service_date: '',
  odometer_at_service: null,
  engine_hours_at_service: null,
  labor_cost: 0,
  parts_cost: 0,
  tax_amount: 0,
  total_cost: 0,
  invoice_number: '',
  payment_method: '',
  payment_status: 'unpaid',
  status: 'draft',
  notes: '',
});

// ── Parts editor row state ──────────────────────────────────────
//
// Parts that haven't been saved yet have id=undefined; the form posts
// them after the work-order create.  Existing parts loaded from the
// server keep their numeric ids so we can DELETE individual rows.

interface DraftPart {
  id?: number;
  part_name: string;
  part_number: string;
  quantity: number;
  unit_cost: number;
  total_cost: number;
  warranty_months: number;
  notes: string;
}

const blankPart = (): DraftPart => ({
  part_name: '',
  part_number: '',
  quantity: 1,
  unit_cost: 0,
  total_cost: 0,
  warranty_months: 0,
  notes: '',
});

// ── Component ────────────────────────────────────────────────────

export default function WorkOrderForm() {
  const { t } = useTranslation();
  const { id: idParam } = useParams<{ id?: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const isEdit = Boolean(idParam && idParam !== 'new');
  const workOrderId = isEdit ? Number(idParam) : null;

  const [wo, setWo] = useState<Partial<WorkOrder>>(blankWorkOrder());
  const [parts, setParts] = useState<DraftPart[]>([]);
  const [attachments, setAttachments] = useState<WorkOrderAttachment[]>([]);
  const [linkedTasks, setLinkedTasks] = useState<MaintenanceTask[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [uploadingFile, setUploadingFile] = useState(false);

  // Hydrate from server in edit mode.  React Query gives us the loading
  // affordance + auto-refetch on revisit.
  const { data: detail, isLoading: detailLoading } = useQuery<WorkOrderDetail>({
    queryKey: ['work-order', workOrderId],
    queryFn: () => apiJSON<WorkOrderDetail>(`/work-orders/${workOrderId}`),
    enabled: isEdit,
  });

  useEffect(() => {
    if (!detail) return;
    setWo(detail.work_order);
    setParts(detail.parts.map(p => ({ ...p })));
    setAttachments(detail.attachments);
    setLinkedTasks(detail.linked_tasks);
  }, [detail]);

  // Auto-totals: parts_cost = sum(parts.total_cost), total_cost =
  // labor + parts + tax.  Recomputed as a derived value so the
  // displayed numbers always match what'll be sent.
  const partsCostComputed = useMemo(
    () => parts.reduce((acc, p) => acc + (Number(p.total_cost) || 0), 0),
    [parts],
  );
  const totalCostComputed = useMemo(
    () => (Number(wo.labor_cost) || 0) + partsCostComputed + (Number(wo.tax_amount) || 0),
    [wo.labor_cost, wo.tax_amount, partsCostComputed],
  );

  // ── Field helpers ──────────────────────────────────────────────
  const setField = <K extends keyof WorkOrder>(key: K, value: WorkOrder[K] | null) =>
    setWo(prev => ({ ...prev, [key]: value }));

  const updatePart = (idx: number, patch: Partial<DraftPart>) =>
    setParts(prev => prev.map((p, i) => {
      if (i !== idx) return p;
      const merged = { ...p, ...patch };
      // Auto-fill total_cost = qty × unit_cost ONLY if the user hasn't
      // manually typed something different.  We detect "hasn't typed"
      // by checking if the prior total_cost equals prior qty × unit.
      const prevAuto = (p.quantity || 0) * (p.unit_cost || 0);
      const prevWasAuto = Math.abs((p.total_cost || 0) - prevAuto) < 0.01;
      if (prevWasAuto && patch.total_cost === undefined) {
        merged.total_cost = (merged.quantity || 0) * (merged.unit_cost || 0);
      }
      return merged;
    }));

  const removePart = async (idx: number) => {
    const target = parts[idx];
    if (target?.id && workOrderId) {
      try {
        await apiJSON(`/work-orders/${workOrderId}/parts/${target.id}`, { method: 'DELETE' });
      } catch (e) {
        toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_part_delete_failed'));
        return;
      }
    }
    setParts(prev => prev.filter((_, i) => i !== idx));
  };

  // ── Save ──────────────────────────────────────────────────────
  //
  // Two-phase save in CREATE mode: first POST the work order to get an
  // id, then POST each unsaved part.  Edit mode is simpler — PUT the
  // work order, POST any newly-added parts (existing parts already
  // exist server-side).  Both modes keep ``parts_cost`` and
  // ``total_cost`` aligned with the computed values so the report
  // aggregations stay accurate.

  const handleSave = async () => {
    if (!wo.vehicle_name?.trim()) {
      setError(t('work_orders_page.toast_vehicle_required'));
      return;
    }
    setSaving(true);
    setError('');
    try {
      const payload = {
        ...wo,
        parts_cost: partsCostComputed,
        total_cost: totalCostComputed,
      };
      let savedId = workOrderId;
      if (isEdit && workOrderId) {
        await apiJSON(`/work-orders/${workOrderId}`, { method: 'PUT', body: payload });
      } else {
        const res = await apiJSON<{ id: number }>('/work-orders', { method: 'POST', body: payload });
        savedId = res.id;
      }
      for (const p of parts) {
        if (p.id || !savedId) continue;
        if (!p.part_name.trim()) continue;
        const { id: _id, ...partPayload } = p;
        await apiJSON(`/work-orders/${savedId}/parts`, { method: 'POST', body: partPayload as Record<string, unknown> });
      }
      qc.invalidateQueries({ queryKey: ['work-orders'] });
      if (savedId) qc.invalidateQueries({ queryKey: ['work-order', savedId] });
      toast.success(isEdit
        ? t('work_orders_page.toast_wo_updated')
        : t('work_orders_page.toast_wo_created', { id: savedId }));
      navigate(`/work-orders/${savedId}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('work_orders_page.toast_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  // ── Attachment upload ─────────────────────────────────────────
  //
  // Only available after the work order exists (we need its id to
  // build the storage folder path).  In CREATE mode the user has to
  // save the draft first before they can attach files.

  const handleUpload = async (file: File, kind: string) => {
    if (!workOrderId) {
      toast.error(t('work_orders_page.toast_save_first'));
      return;
    }
    setUploadingFile(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await apiFetch(
        `/work-orders/${workOrderId}/attachments?kind=${encodeURIComponent(kind)}`,
        { method: 'POST', body: fd },
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        toast.error(typeof err.detail === 'string' ? err.detail : t('work_orders_page.toast_upload_failed'));
        return;
      }
      toast.success(t('work_orders_page.toast_uploaded', { name: file.name }));
      qc.invalidateQueries({ queryKey: ['work-order', workOrderId] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_upload_failed'));
    } finally {
      setUploadingFile(false);
    }
  };

  const handleDeleteAttachment = async (att: WorkOrderAttachment) => {
    if (!workOrderId) return;
    if (!window.confirm(t('work_orders_page.confirm_delete_attachment', { name: att.file_name }))) return;
    try {
      await apiJSON(`/work-orders/${workOrderId}/attachments/${att.id}`, { method: 'DELETE' });
      qc.invalidateQueries({ queryKey: ['work-order', workOrderId] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_delete_failed'));
    }
  };

  if (isEdit && detailLoading) {
    return (
      <div>
        <PageHeader icon={FileText} title={t('work_orders_page.loading_title')} description={t('work_orders_page.loading_desc')} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        icon={FileText}
        title={isEdit ? t('work_orders_page.form_title_edit', { id: workOrderId }) : t('work_orders_page.form_title_new')}
        description={isEdit ? t('work_orders_page.form_desc_edit') : t('work_orders_page.form_desc_new')}
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => navigate('/work-orders')}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md text-xs font-medium text-foreground transition border border-border"
            >
              <ArrowLeft size={14} />
              {t('work_orders_page.back')}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary hover:bg-primary/90 disabled:opacity-50 rounded-md text-xs font-medium text-primary-foreground transition"
            >
              <Save size={14} />
              {saving ? t('work_orders_page.saving') : isEdit ? t('work_orders_page.save_changes') : t('work_orders_page.create_draft')}
            </button>
          </div>
        }
      />

      {error && (
        <div className="mb-4">
          <ErrorState message={error} />
        </div>
      )}

      {/* ── Vehicle + status block ─────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
          <Receipt size={14} className="text-muted-foreground" />
          {t('work_orders_page.section_shop_visit')}
        </h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label={t('work_orders_page.field_vehicle')} required>
            <input
              type="text"
              value={wo.vehicle_name || ''}
              onChange={e => setField('vehicle_name', e.target.value)}
              placeholder={t('work_orders_page.ph_vehicle')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_vehicle_type')}>
            <select
              value={wo.vehicle_type || ''}
              onChange={e => setField('vehicle_type', e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm text-foreground focus:outline-none focus:border-ring"
            >
              <option value="">{t('work_orders_page.vehicle_type_unset')}</option>
              <option value="truck">{t('work_orders_page.vehicle_type_truck')}</option>
              <option value="trailer">{t('work_orders_page.vehicle_type_trailer')}</option>
            </select>
          </Field>
          <Field label={t('work_orders_page.field_service_date')}>
            <input
              type="date"
              value={(wo.service_date || '').slice(0, 10)}
              onChange={e => setField('service_date', e.target.value || null)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_status')}>
            <select
              value={wo.status || 'draft'}
              onChange={e => setField('status', e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring capitalize"
            >
              {['draft', 'submitted', 'paid', 'void'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
          <Field label={t('work_orders_page.field_odometer')}>
            <input
              type="number" min="0" step="1"
              value={wo.odometer_at_service ?? ''}
              onChange={e => setField('odometer_at_service', e.target.value ? Number(e.target.value) : null)}
              placeholder={t('work_orders_page.ph_odometer')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_engine_hours')}>
            <input
              type="number" min="0" step="1"
              value={wo.engine_hours_at_service ?? ''}
              onChange={e => setField('engine_hours_at_service', e.target.value ? Number(e.target.value) : null)}
              placeholder={t('work_orders_page.ph_engine_hours')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* ── Vendor block ───────────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_vendor')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label={t('work_orders_page.field_vendor_name')}>
            <input
              type="text"
              value={wo.vendor_name || ''}
              onChange={e => setField('vendor_name', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_name')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_vendor_phone')}>
            <input
              type="tel"
              value={wo.vendor_phone || ''}
              onChange={e => setField('vendor_phone', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_phone')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_vendor_address')}>
            <input
              type="text"
              value={wo.vendor_address || ''}
              onChange={e => setField('vendor_address', e.target.value)}
              placeholder={t('work_orders_page.ph_vendor_address')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* ── Parts editor ───────────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold">{t('work_orders_page.section_parts')}</h3>
            <p className="text-2xs text-muted-foreground mt-0.5">
              ↻ {t('work_orders_page.parts_hint')}
            </p>
          </div>
          <button
            type="button"
            onClick={() => setParts(prev => [...prev, blankPart()])}
            className="inline-flex items-center gap-1 text-xs px-2 py-1 bg-muted hover:bg-muted/80 border border-border rounded"
          >
            <Plus size={12} />
            {t('work_orders_page.add_part')}
          </button>
        </div>
        {parts.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t('work_orders_page.no_parts')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[680px]">
              <thead>
                <tr className="text-xs text-muted-foreground border-b border-border">
                  <th className="text-left font-medium py-1.5 px-2">{t('work_orders_page.col_part')}</th>
                  <th className="text-left font-medium py-1.5 px-2 w-28">{t('work_orders_page.col_part_number')}</th>
                  <th className="text-right font-medium py-1.5 px-2 w-16">{t('work_orders_page.col_qty')}</th>
                  <th className="text-right font-medium py-1.5 px-2 w-24">{t('work_orders_page.col_unit')}</th>
                  <th className="text-right font-medium py-1.5 px-2 w-24">{t('work_orders_page.col_total')}</th>
                  <th className="text-right font-medium py-1.5 px-2 w-20">{t('work_orders_page.col_warranty')}</th>
                  <th className="py-1.5 px-2 w-8"></th>
                </tr>
              </thead>
              <tbody>
                {parts.map((p, idx) => (
                  <tr key={p.id ?? `new-${idx}`} className="border-b border-border/40">
                    <td className="py-1.5 px-2">
                      <input
                        type="text"
                        value={p.part_name}
                        onChange={e => updatePart(idx, { part_name: e.target.value })}
                        placeholder={t('work_orders_page.ph_part_name')}
                        className="w-full bg-transparent border-0 px-1 py-0.5 text-sm focus:outline-none focus:bg-muted/40 rounded"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="text"
                        value={p.part_number}
                        onChange={e => updatePart(idx, { part_number: e.target.value })}
                        placeholder={t('work_orders_page.ph_part_number')}
                        className="w-full bg-transparent border-0 px-1 py-0.5 text-xs font-mono focus:outline-none focus:bg-muted/40 rounded"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number" min="0" step="1"
                        value={p.quantity}
                        onChange={e => updatePart(idx, { quantity: Number(e.target.value) || 0 })}
                        className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number" min="0" step="0.01"
                        value={p.unit_cost}
                        onChange={e => updatePart(idx, { unit_cost: Number(e.target.value) || 0 })}
                        className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number" min="0" step="0.01"
                        value={p.total_cost}
                        onChange={e => updatePart(idx, { total_cost: Number(e.target.value) || 0 })}
                        className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums font-medium focus:outline-none focus:bg-muted/40 rounded"
                      />
                    </td>
                    <td className="py-1.5 px-2">
                      <input
                        type="number" min="0" step="1"
                        value={p.warranty_months}
                        onChange={e => updatePart(idx, { warranty_months: Number(e.target.value) || 0 })}
                        placeholder="0"
                        className="w-full bg-transparent border-0 px-1 py-0.5 text-sm text-right tabular-nums focus:outline-none focus:bg-muted/40 rounded"
                      />
                    </td>
                    <td className="py-1.5 px-2 text-right">
                      <button
                        type="button"
                        onClick={() => removePart(idx)}
                        className="text-muted-foreground hover:text-destructive p-1"
                        title={t('work_orders_page.remove_part')}
                        aria-label={t('work_orders_page.remove_part')}
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* ── Cost summary block ─────────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_costs')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label={t('work_orders_page.field_labor')}>
            <input
              type="number" min="0" step="0.01"
              value={wo.labor_cost ?? 0}
              onChange={e => setField('labor_cost', Number(e.target.value) || 0)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_tax')}>
            <input
              type="number" min="0" step="0.01"
              value={wo.tax_amount ?? 0}
              onChange={e => setField('tax_amount', Number(e.target.value) || 0)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm tabular-nums focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
        <div className="mt-3 pt-3 border-t border-border/50 flex flex-wrap gap-x-6 gap-y-1 text-sm">
          <span className="text-muted-foreground">{t('work_orders_page.sum_parts')}: <span className="font-medium tabular-nums text-foreground">${partsCostComputed.toFixed(2)}</span></span>
          <span className="text-muted-foreground">{t('work_orders_page.sum_labor')}: <span className="font-medium tabular-nums text-foreground">${(Number(wo.labor_cost) || 0).toFixed(2)}</span></span>
          <span className="text-muted-foreground">{t('work_orders_page.sum_tax')}: <span className="font-medium tabular-nums text-foreground">${(Number(wo.tax_amount) || 0).toFixed(2)}</span></span>
          <span className="text-foreground font-semibold ml-auto">{t('work_orders_page.sum_total')}: <span className="tabular-nums">${totalCostComputed.toFixed(2)}</span></span>
        </div>
      </section>

      {/* ── Invoice / payment block ────────────────────────────── */}
      <section className="bg-card border border-border rounded-xl p-5 mb-5">
        <h3 className="text-sm font-semibold mb-3">{t('work_orders_page.section_invoice')}</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label={t('work_orders_page.field_invoice_number')}>
            <input
              type="text"
              value={wo.invoice_number || ''}
              onChange={e => setField('invoice_number', e.target.value)}
              placeholder={t('work_orders_page.ph_invoice_number')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm font-mono focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_payment_method')}>
            <input
              type="text"
              value={wo.payment_method || ''}
              onChange={e => setField('payment_method', e.target.value)}
              placeholder={t('work_orders_page.ph_payment_method')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
          <Field label={t('work_orders_page.field_payment_status')}>
            <select
              value={wo.payment_status || 'unpaid'}
              onChange={e => setField('payment_status', e.target.value)}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring capitalize"
            >
              {['unpaid', 'paid', 'partial', 'void'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </Field>
        </div>
        <div className="mt-3">
          <Field label={t('work_orders_page.field_notes')}>
            <textarea
              rows={3}
              value={wo.notes || ''}
              onChange={e => setField('notes', e.target.value)}
              placeholder={t('work_orders_page.ph_notes')}
              className="w-full bg-muted border border-border rounded px-2.5 py-1.5 text-sm focus:outline-none focus:border-ring"
            />
          </Field>
        </div>
      </section>

      {/* In create mode the attachments section can't render yet (no
          work-order id → no folder path).  Show a prominent banner
          where the section would be so the user understands why the
          upload UI is missing and doesn't scroll past hunting for it. */}
      {!isEdit && (
        <section className={`mb-5 p-4 rounded-xl text-sm inline-flex items-start gap-2 w-full ${toneClasses('info')}`}>
          <Paperclip size={16} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-medium">{t('work_orders_page.attachments_unlock_title')}</p>
            <p className="text-xs mt-0.5 text-info">
              {t('work_orders_page.attachments_unlock_desc')}
            </p>
          </div>
        </section>
      )}

      {/* ── Attachments (edit mode only) ──────────────────────── */}
      {isEdit && (
        <section className="bg-card border border-border rounded-xl p-5 mb-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold inline-flex items-center gap-1.5">
              <Paperclip size={14} className="text-muted-foreground" />
              {t('work_orders_page.section_attachments')}
            </h3>
            <div className="flex items-center gap-2">
              <AttachUploadButton label={t('work_orders_page.add_invoice')} kind="invoice" onUpload={handleUpload} disabled={uploadingFile} accept=".pdf,image/*" />
              <AttachUploadButton label={t('work_orders_page.add_photo')} kind="photo" onUpload={handleUpload} disabled={uploadingFile} accept="image/*" />
            </div>
          </div>
          {attachments.length === 0 ? (
            <p className="text-xs text-muted-foreground">{t('work_orders_page.no_attachments')}</p>
          ) : (
            <ul className="space-y-2">
              {attachments.map(att => (
                <AttachmentRow
                  key={att.id}
                  attachment={att}
                  workOrderId={workOrderId!}
                  onDelete={() => handleDeleteAttachment(att)}
                />
              ))}
            </ul>
          )}
        </section>
      )}

      {/* ── Linked maintenance tasks (edit mode) ──────────────── */}
      {isEdit && linkedTasks.length > 0 && (
        <section className="bg-card border border-border rounded-xl p-5 mb-5">
          <h3 className="text-sm font-semibold mb-3 inline-flex items-center gap-1.5">
            <LinkIcon size={14} className="text-muted-foreground" />
            {t('work_orders_page.section_linked_tasks')}
          </h3>
          <ul className="space-y-1.5">
            {linkedTasks.map(task => (
              <li key={task.id} className="text-sm flex items-center gap-2">
                <span className="font-mono text-xs text-muted-foreground">#{task.id}</span>
                <span className="capitalize">{(task.task_type || '').replace(/_/g, ' ')}</span>
                <span className="text-muted-foreground">— {task.description || t('work_orders_page.no_description')}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {!isEdit && (
        <p className="text-xs text-muted-foreground mb-4">
          💡 {t('work_orders_page.save_first_hint')}
        </p>
      )}
    </div>
  );
}

// ── Small subcomponents ────────────────────────────────────────

function Field({ label, required, children }: { label: string; required?: boolean; children: React.ReactNode }) {
  // Wrap children inside <label> so the input/select/textarea is
  // implicitly associated — screen readers announce the label when the
  // field receives focus, and clicking the text focuses the control.
  return (
    <label className="block">
      <span className="block text-xs text-muted-foreground mb-1">
        {label}{required && <span className="text-destructive ml-0.5">*</span>}
      </span>
      {children}
    </label>
  );
}

function AttachUploadButton({
  label, kind, onUpload, disabled, accept,
}: {
  label: string;
  kind: string;
  onUpload: (file: File, kind: string) => Promise<void>;
  disabled: boolean;
  accept: string;
}) {
  // Native <input type=file> hidden behind a styled label so the click
  // affordance is a real button.  Resets the input value after upload
  // so the same file can be re-attached if the user deletes + re-adds.
  return (
    <label className={`inline-flex items-center gap-1 text-xs px-2 py-1 bg-muted hover:bg-muted/80 border border-border rounded cursor-pointer ${disabled ? 'opacity-50 cursor-not-allowed' : ''}`}>
      <Plus size={12} />
      {label}
      <input
        type="file"
        accept={accept}
        className="hidden"
        disabled={disabled}
        onChange={async (e) => {
          const f = e.target.files?.[0];
          if (f) await onUpload(f, kind);
          e.target.value = '';
        }}
      />
    </label>
  );
}

function AttachmentRow({
  attachment, workOrderId, onDelete,
}: {
  attachment: WorkOrderAttachment;
  workOrderId: number;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const isImage = attachment.content_type?.startsWith('image/');
  const handleDownload = async () => {
    try {
      const res = await apiFetch(`/work-orders/${workOrderId}/attachments/${attachment.id}`);
      if (!res.ok) {
        toast.error(`${t('work_orders_page.toast_download_failed')}: ${res.statusText}`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = attachment.file_name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('work_orders_page.toast_download_failed'));
    }
  };

  return (
    <li className="flex items-center gap-3 p-2.5 bg-muted/40 border border-border rounded-lg">
      <div className="w-8 h-8 rounded bg-muted flex items-center justify-center shrink-0">
        {isImage ? <ImageIcon size={16} className="text-muted-foreground" /> : <FileText size={16} className="text-muted-foreground" />}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium truncate">{attachment.file_name}</p>
        <p className="text-xs text-muted-foreground">
          <span className="capitalize">{attachment.kind}</span>
          {' · '}
          {(attachment.file_size / 1024).toFixed(1)} KB
          {attachment.uploaded_by_name && ` · ${t('work_orders_page.uploaded_by', { name: attachment.uploaded_by_name })}`}
          {' · '}
          {new Date(attachment.uploaded_at).toLocaleDateString()}
        </p>
      </div>
      <button
        type="button"
        onClick={handleDownload}
        className="text-xs text-primary hover:underline"
      >
        {t('work_orders_page.download')}
      </button>
      <button
        type="button"
        onClick={onDelete}
        className="text-muted-foreground hover:text-destructive p-1"
        title={t('work_orders_page.delete_attachment')}
        aria-label={t('work_orders_page.delete_attachment')}
      >
        <X size={14} />
      </button>
    </li>
  );
}
