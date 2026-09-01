/**
 * Post-approve source-file archival — the client half of every AI
 * action that creates a record files belong on.
 *
 * Chat attachments are transient server-side by design (the file lives
 * on the device), so when an approved action creates a record the
 * files belong on, THIS uploads them: the executor echoes the
 * ``source_files`` NAMES, the approve response's ``target_id`` is the
 * ONLY id we upload to (never anything from the model-authored
 * payload), and the names are exact-match lookup keys into the local
 * attachment store — never paths or URLs.
 *
 * Only image attachments can be archived: the store holds a faithful
 * compressed data-URL for those, while a PDF keeps only its extracted
 * TEXT — the bytes are genuinely not on hand to re-send.  So a PDF
 * reports as `missing` and the card tells the user to attach the
 * original on the record's own page.  That is a store limitation, not
 * a filter to widen; widening it would upload a text file wearing a
 * PDF's name.
 *
 * TWO destinations now, dispatched on the executor's ``target_type``:
 * a work order takes attachments, a vehicle document IS the file and
 * goes to that truck's documents endpoint carrying the approved type
 * and dates.  Adding a third means adding a case here, not a second
 * uploader.
 */
import { apiFetch } from '../../api/client';
import { findHeldAttachments } from './attachmentStore';

export interface SourceUploadOutcome {
  uploaded: string[];
  /** Not held on this device (or not re-uploadable, e.g. a PDF). */
  missing: string[];
  failed: string[];
}

/** Where an approved action's files go, and what rides with them. */
export interface UploadTarget {
  target_type?: unknown;
  target_id?: unknown;
  doc_type?: unknown;
  issued_at?: unknown;
  expires_at?: unknown;
}

/** The endpoint + extra form fields for one approve result, or null
 *  when this action's result is not something files attach to. */
function routeFor(r: UploadTarget): { url: string; fields: [string, string][] } | null {
  const id = String(r.target_id ?? '');
  if (!id) return null;
  if (r.target_type === 'work_order') {
    return { url: `/work-orders/${id}/attachments?kind=photo`, fields: [] };
  }
  if (r.target_type === 'vehicle_document') {
    // The approved metadata travels WITH the file — the upload
    // endpoint is the same one the dialog posts to, so the expiry the
    // human approved is the expiry that gets stored.
    const fields: [string, string][] = [
      ["doc_type", String(r.doc_type || "other")],
    ];
    if (r.issued_at) fields.push(["issued_at", String(r.issued_at)]);
    if (r.expires_at) fields.push(["expires_at", String(r.expires_at)]);
    return { url: `/vehicles/registry/${id}/documents`, fields };
  }
  return null;
}

export async function uploadSourceFiles(
  result: UploadTarget, names: string[],
): Promise<SourceUploadOutcome> {
  const route = routeFor(result);
  if (!route) {
    return { uploaded: [], missing: [...names], failed: [] };
  }
  return _upload(route, names);
}

/** Back-compat for the work-order call site. */
export async function uploadSourceFilesToWorkOrder(
  workOrderId: number, names: string[],
): Promise<SourceUploadOutcome> {
  return uploadSourceFiles(
    { target_type: 'work_order', target_id: workOrderId }, names);
}

async function _upload(
  route: { url: string; fields: [string, string][] }, names: string[],
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
      for (const [k, v] of route.fields) fd.append(k, v);
      const res = await apiFetch(
        route.url,
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
