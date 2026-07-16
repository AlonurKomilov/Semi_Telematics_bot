/**
 * Bridge hook: "create a work order from this fault / defect", with a
 * duplicate guard.
 *
 * Both entry points (a fault row, a failed inspection item) call the
 * same ``createFrom(prefill)``.  Before navigating it asks the server
 * for the vehicle's OPEN work orders and looks for one whose complaint
 * already matches — the "clicked twice on the same fault" case.  If it
 * finds one, it opens a confirm dialog offering to open the existing
 * one, create another anyway, or cancel; otherwise it navigates
 * straight to the pre-filled create form.
 *
 * Usage:
 *   const { createFrom, bridgeDialog } = useWorkOrderBridge();
 *   ... <button onClick={() => createFrom(prefill)}>+ Work order</button>
 *   ... {bridgeDialog}   // render once in the component tree
 */
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiJSON } from '../../api/client';
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from '../../components/ui/dialog';
import { Button } from '../../components/ui/button';
import type { WorkOrder } from '../../types';
import { WO_PREFILL_STATE_KEY, type WorkOrderPrefill } from './createFrom';

// A work order is still "open" (worth warning about) while it's a
// draft or submitted AND not yet settled — a paid/void one is a closed
// ticket for a past instance of the same problem and shouldn't block a
// fresh repair.  (status carries lifecycle only since migration 154;
// money truth is payment_status.)
const OPEN_STATUSES = new Set(['draft', 'submitted']);
const isOpenWorkOrder = (w: { status?: string; payment_status?: string }) =>
  OPEN_STATUSES.has(String(w.status ?? '')) &&
  !['paid', 'void'].includes(String(w.payment_status ?? ''));

const norm = (s: string | undefined) => (s ?? '').trim().toLowerCase();

export function useWorkOrderBridge() {
  const navigate = useNavigate();
  const [pending, setPending] = useState<
    { prefill: WorkOrderPrefill; existing: WorkOrder } | null
  >(null);
  const [checking, setChecking] = useState(false);

  const goCreate = (prefill: WorkOrderPrefill) =>
    navigate('/work-orders/new', { state: { [WO_PREFILL_STATE_KEY]: prefill } });

  const createFrom = async (prefill: WorkOrderPrefill) => {
    // No vehicle or no complaint → nothing to match on; just create.
    if (!prefill.vehicle_name || !prefill.complaint) { goCreate(prefill); return; }
    setChecking(true);
    try {
      const res = await apiJSON<{ work_orders: WorkOrder[] }>(
        `/work-orders?vehicle=${encodeURIComponent(prefill.vehicle_name)}`,
      );
      const wantComplaint = norm(prefill.complaint);
      const match = (res.work_orders ?? []).find(
        (w) => isOpenWorkOrder(w) && norm(w.complaint) === wantComplaint,
      );
      if (match) { setPending({ prefill, existing: match }); return; }
      goCreate(prefill);
    } catch {
      // If the pre-check fails (network / perms), don't block the
      // operator — fall through to the create form.
      goCreate(prefill);
    } finally {
      setChecking(false);
    }
  };

  const bridgeDialog = pending ? (
    <Dialog open onOpenChange={(o) => { if (!o) setPending(null); }}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Work order already open</DialogTitle>
          <DialogDescription>
            {pending.existing.vehicle_name} already has an open work order
            {pending.existing.id ? ` (#${pending.existing.id})` : ''} for this
            same issue. Open the existing one, or create another anyway?
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex-col-reverse sm:flex-row sm:justify-end gap-2">
          <Button variant="ghost" onClick={() => setPending(null)}>Cancel</Button>
          <Button
            variant="outline"
            onClick={() => { const id = pending.existing.id; setPending(null); navigate(`/work-orders/${id}`); }}
          >
            Open existing
          </Button>
          <Button
            onClick={() => { const p = pending.prefill; setPending(null); goCreate(p); }}
          >
            Create anyway
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ) : null;

  return { createFrom, bridgeDialog, checking };
}
