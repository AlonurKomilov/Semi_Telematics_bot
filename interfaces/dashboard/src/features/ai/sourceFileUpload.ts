/**
 * Post-approve source-file archival — the client half of the AI
 * create-work-order contract.
 *
 * Chat attachments are transient server-side by design (the file lives
 * on the device), so when an approved action creates a record the
 * files belong on, THIS uploads them: the executor echoes the
 * ``source_files`` NAMES, the approve response's ``target_id`` is the
 * ONLY id we upload to (never anything from the model-authored
 * payload), and the names are exact-match lookup keys into the local
 * attachment store — never paths or URLs.
 *
 * Only image attachments can be archived (the store holds a faithful
 * compressed data-URL); documents keep just their extracted text, so a
 * PDF reports as `missing` and the card tells the user to attach the
 * original on the record's page.
 */
import { apiFetch } from '../../api/client';
import { findHeldAttachments } from './attachmentStore';

export interface SourceUploadOutcome {
  uploaded: string[];
  /** Not held on this device (or not re-uploadable, e.g. a PDF). */
  missing: string[];
  failed: string[];
}

export async function uploadSourceFilesToWorkOrder(
  workOrderId: number, names: string[],
): Promise<SourceUploadOutcome> {
  const held = new Map(findHeldAttachments(names).map((a) => [a.name, a]));
  const out: SourceUploadOutcome = { uploaded: [], missing: [], failed: [] };
  for (const name of names) {
    const att = held.get(name);
    if (!att || att.kind !== 'image' || !att.content.startsWith('data:')) {
      out.missing.push(name);
      continue;
    }
    try {
      const blob = await (await fetch(att.content)).blob();
      const fd = new FormData();
      fd.append('file', new File([blob], name, { type: blob.type }));
      const res = await apiFetch(
        `/work-orders/${workOrderId}/attachments?kind=photo`,
        { method: 'POST', body: fd },
      );
      if (res.ok) out.uploaded.push(name);
      else out.failed.push(name);
    } catch {
      out.failed.push(name);
    }
  }
  return out;
}
