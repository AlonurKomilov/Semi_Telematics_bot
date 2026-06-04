import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { PenLine } from 'lucide-react';
import { apiJSON } from '../../api/client';
import { usePermissions } from '../../hooks/usePermissions';
import type { PTIInspectionDetail } from '../../types';
import { SignaturePad } from './SignaturePad';

/**
 * Renders the driver + reviewer signatures section for an inspection.
 *
 * Driver: always read-only; the signature was captured at submit time
 * in the Mini App.  Reviewer: read-only once captured; otherwise a
 * "Co-sign" button is offered when the caller has ``can_inspections_all``
 * and the inspection is currently in the ``submitted`` state (you can
 * only co-sign something that's been driver-submitted).
 *
 * Empty state for either role renders as a muted placeholder so the
 * section is always visually present — that consistency helps fleet
 * reviewers spot a missing signature at a glance.
 */

interface Props {
  inspection: PTIInspectionDetail;
  /** Called after a successful co-sign so the parent can refresh. */
  onSigned: () => void;
}

function _formatTs(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString();
}

function SignatureBox({
  label,
  pngDataUrl,
  signedAt,
  signerName,
  emptyText,
}: {
  label: string;
  pngDataUrl: string | null | undefined;
  signedAt: string | null | undefined;
  signerName?: string;
  emptyText: string;
}) {
  return (
    <div className="border border-border rounded-md p-3 space-y-2 flex-1">
      <p className="text-xs text-muted-foreground font-medium">{label}</p>
      {pngDataUrl ? (
        <>
          <div className="border border-border rounded bg-white">
            <img
              src={pngDataUrl}
              alt={label}
              className="w-full h-24 object-contain"
            />
          </div>
          <p className="text-[11px] text-muted-foreground">
            {signerName ? `${signerName} · ` : ''}{_formatTs(signedAt)}
          </p>
        </>
      ) : (
        <div className="border border-dashed border-border rounded h-24 flex items-center justify-center bg-muted/30">
          <span className="text-xs text-muted-foreground">{emptyText}</span>
        </div>
      )}
    </div>
  );
}

export function SignaturesPanel({ inspection: ins, onSigned }: Props) {
  const { t } = useTranslation();
  const { has } = usePermissions();
  const [signing, setSigning] = useState(false);
  const [saving, setSaving] = useState(false);

  // Co-sign only makes sense when (a) the caller is a fleet reviewer
  // and (b) the inspection has actually been submitted.  Anything
  // else either lacks authority (drivers) or has nothing to co-sign on
  // (an in-progress inspection isn't finalized yet).
  const canCoSign =
    has('can_inspections_all')
    && ins.status === 'submitted'
    && !ins.reviewer_signature;

  const submitSignature = async (dataUrl: string) => {
    setSaving(true);
    try {
      await apiJSON(`/inspections/${ins.id}/signature`, {
        method: 'POST',
        body: { role: 'reviewer', signature_data: dataUrl },
      });
      toast.success(t('inspections.signature.saved'));
      setSigning(false);
      onSigned();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : t('inspections.signature.save_failed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-5 py-4 border-b border-border space-y-3">
      <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
        {t('inspections.signature.section_title')}
      </p>
      <div className="flex flex-col sm:flex-row gap-3">
        <SignatureBox
          label={t('inspections.signature.driver_label')}
          pngDataUrl={ins.driver_signature}
          signedAt={ins.driver_signed_at}
          signerName={ins.driver_name}
          emptyText={t('inspections.signature.driver_empty')}
        />
        <SignatureBox
          label={t('inspections.signature.reviewer_label')}
          pngDataUrl={ins.reviewer_signature}
          signedAt={ins.reviewer_signed_at}
          signerName={ins.reviewed_by_name}
          emptyText={t('inspections.signature.reviewer_empty')}
        />
      </div>

      {canCoSign && !signing && (
        <button
          type="button"
          onClick={() => setSigning(true)}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border hover:bg-muted"
        >
          <PenLine size={12} />
          {t('inspections.signature.co_sign')}
        </button>
      )}

      {signing && (
        <SignaturePad
          prompt={t('inspections.signature.co_sign_prompt')}
          saving={saving}
          onCancel={() => { if (!saving) setSigning(false); }}
          onConfirm={submitSignature}
        />
      )}
    </div>
  );
}
