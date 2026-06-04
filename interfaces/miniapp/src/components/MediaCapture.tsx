import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { haptics } from '../hooks/useTelegram';
import { apiFetch } from '../api/client';

/**
 * Inline media capture used by the PTI wizard.
 *
 * Why ``<input type="file" capture>`` instead of ``getUserMedia()``:
 * the Telegram WebView wraps Safari/Chrome and respects the capture
 * attribute — tapping the button opens the OS camera directly.
 * ``getUserMedia`` works too but draws a preview canvas which is more
 * code for no UX benefit (Telegram already gates the camera permission
 * at the OS layer).
 *
 * Client-side resize is done before upload because the user is on a
 * cellular link in a yard with 1-2 bars.  A 12 MP photo from a modern
 * Android is 4-6 MB at full quality; the canvas-resize step takes it
 * to ~400 KB at 1920px @ 0.85 quality with no visible loss for an
 * inspection photo.
 */

type MediaKind = 'photo' | 'video';

/** Shape of the upload endpoint's JSON response (subset we use). */
interface UploadedMedia {
  id: number;
  media_type: 'photo' | 'video';
}

interface Props {
  inspectionId: number;
  itemKey?: string;   // omit for general inspection media
  /** Called after a successful upload.  Receives the new media row so
      the caller can fire a per-photo AI check. */
  onUploaded: (uploaded?: UploadedMedia) => void;
  /** Disable the button while the inspection is read-only. */
  disabled?: boolean;
}

const MAX_DIMENSION = 1920;
const JPEG_QUALITY  = 0.85;
const UPLOAD_ENDPOINT = '/api/inspections';


async function resizeImage(file: File): Promise<Blob> {
  // Use ``createImageBitmap`` where available (faster + off the main
  // thread) and fall back to an ``<img>`` decode for Safari.
  let bitmap: ImageBitmap | HTMLImageElement;
  if (typeof createImageBitmap === 'function') {
    try {
      bitmap = await createImageBitmap(file);
    } catch {
      bitmap = await imgFallback(file);
    }
  } else {
    bitmap = await imgFallback(file);
  }
  const w = (bitmap as ImageBitmap).width  || (bitmap as HTMLImageElement).naturalWidth;
  const h = (bitmap as ImageBitmap).height || (bitmap as HTMLImageElement).naturalHeight;
  const scale = Math.min(1, MAX_DIMENSION / Math.max(w, h));
  const targetW = Math.round(w * scale);
  const targetH = Math.round(h * scale);
  const canvas = document.createElement('canvas');
  canvas.width  = targetW;
  canvas.height = targetH;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('canvas unavailable');
  ctx.drawImage(bitmap as CanvasImageSource, 0, 0, targetW, targetH);
  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (b) => b ? resolve(b) : reject(new Error('toBlob failed')),
      'image/jpeg',
      JPEG_QUALITY,
    );
  });
}

function imgFallback(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => { URL.revokeObjectURL(url); resolve(img); };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('image decode')); };
    img.src = url;
  });
}


export function MediaCapture({ inspectionId, itemKey, onUploaded, disabled }: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const photoInputRef = useRef<HTMLInputElement>(null);
  const videoInputRef = useRef<HTMLInputElement>(null);

  const upload = async (file: File, kind: MediaKind) => {
    setBusy(true);
    setError(null);
    try {
      // Photos go through canvas resize to keep upload tiny.  Videos
      // are passed as-is; the browser already records to a reasonable
      // bitrate and Telegram's WebView lacks WebCodecs for re-encode.
      const blob: Blob = kind === 'photo' ? await resizeImage(file) : file;
      const formData = new FormData();
      // Set a meaningful filename so the server-side ObjectStore
      // doesn't store the raw "blob" name iOS sends.
      const ext = kind === 'photo' ? 'jpg' : (file.name.split('.').pop() || 'mp4');
      const ts = Date.now();
      const filename = `${kind}_${itemKey || 'general'}_${ts}.${ext}`;
      formData.append('file', blob, filename);

      const url = itemKey
        ? `${UPLOAD_ENDPOINT}/${inspectionId}/media?item_key=${encodeURIComponent(itemKey)}`
        : `${UPLOAD_ENDPOINT}/${inspectionId}/media`;
      const res = await apiFetch(url, { method: 'POST', body: formData });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // ``detail`` may be a plain string or a structured
        // {message, error_code} object (e.g. expired Drive token).
        const d = body?.detail;
        const detailMsg = typeof d === 'string'
          ? d
          : (d && typeof d === 'object' && typeof d.message === 'string')
            ? d.message
            : `Upload failed (${res.status})`;
        throw new Error(detailMsg);
      }
      const created = await res.json().catch(() => null) as UploadedMedia | null;
      haptics.success();
      onUploaded(created ?? undefined);
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Upload failed';
      setError(msg);
      haptics.error();
    } finally {
      setBusy(false);
    }
  };

  const onPickPhoto = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ''; // reset so re-selecting the same file fires onChange
    if (file) upload(file, 'photo');
  };

  const onPickVideo = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (file) upload(file, 'video');
  };

  return (
    <div className="media-capture">
      <input
        ref={photoInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        style={{ display: 'none' }}
        onChange={onPickPhoto}
        disabled={disabled || busy}
      />
      <input
        ref={videoInputRef}
        type="file"
        accept="video/*"
        capture="environment"
        style={{ display: 'none' }}
        onChange={onPickVideo}
        disabled={disabled || busy}
      />
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          type="button"
          className="media-capture__btn"
          onClick={() => { haptics.selection(); photoInputRef.current?.click(); }}
          disabled={disabled || busy}
        >
          {busy ? t('pti.uploading') : `📷 ${t('pti.take_photo')}`}
        </button>
        <button
          type="button"
          className="media-capture__btn"
          onClick={() => { haptics.selection(); videoInputRef.current?.click(); }}
          disabled={disabled || busy}
        >
          {busy ? t('pti.uploading') : `🎥 ${t('pti.record_video')}`}
        </button>
      </div>
      {error && (
        <p className="media-capture__error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
