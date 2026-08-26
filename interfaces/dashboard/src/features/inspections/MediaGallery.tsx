import { useMemo, useState } from 'react';
import { Download, FileText, Pencil, X } from 'lucide-react';
import { apiFetch } from '../../api/client';
import { useTimezone } from '../../hooks/useTimezone';
import { formatDate } from '../../utils/datetime';
import type { PTIInspectionDetail, PTIInspectionMedia } from '../../types';
import { Dialog, DialogContent, DialogTitle } from '../../components/ui/dialog';
import { parseVerdict, VERDICT_EMOJI, verdictTone } from './aiVerdict';
import { Badge } from '@/components/ui/badge';

// Solid-fill class for the AI verdict dot on a thumbnail.  Derives from
// the shared verdict→tone map so the dot can't drift from the item-list
// pill colour; ``neutral`` has no solid hue so it falls back to the
// muted-foreground fill.
const VERDICT_DOT_BG: Record<string, string> = {
  ok: 'bg-ok', warn: 'bg-warn', danger: 'bg-danger', info: 'bg-info', neutral: 'bg-muted-foreground',
};

interface Props {
  inspection: PTIInspectionDetail;
}

/**
 * Photo + video gallery for the inspection detail drawer.
 *
 * Thumbnails are streamed directly from
 * ``/api/inspections/{id}/media/{media_id}`` — same auth path as work
 * orders.  Click → lightbox modal with full-size image / ``<video
 * controls>``.  Download button writes the file to disk via a
 * synthetic ``<a download>`` click (works in all modern browsers; the
 * blob comes from a fresh ``apiFetch`` so the Bearer token rides
 * along).
 */


type GroupKey = string;  // either 'general' or the item_key


function ThumbnailImage({ src, alt }: { src: string; alt: string }) {
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      className="w-full h-full object-cover rounded-md"
    />
  );
}


function ThumbnailVideo({ src, alt: _alt }: { src: string; alt: string }) {
  // ``poster`` would be cleaner but we have no thumbnail extractor on
  // the server.  Show the first frame via metadata preload.
  return (
    <video
      src={src}
      preload="metadata"
      className="w-full h-full object-cover rounded-md bg-black"
      muted
    />
  );
}


async function downloadMedia(inspectionId: number, media: PTIInspectionMedia) {
  // The browser's native download UI doesn't carry our Bearer token,
  // so we pull the bytes via apiFetch + create an object URL.
  const res = await apiFetch(`/inspections/${inspectionId}/media/${media.id}`);
  if (!res.ok) return;
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = media.file_name || `media-${media.id}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}


export function MediaGallery({ inspection }: Props) {
  const tz = useTimezone();
  const media = inspection.media ?? [];
  const [lightboxIdx, setLightboxIdx] = useState<number | null>(null);

  // Group media by source item so the gallery reads "Tires walkaround"
  // then the 4 tire photos under it, rather than a flat alphabetical
  // soup of 18 photos.
  const grouped = useMemo(() => {
    const groups: Record<GroupKey, { label: string; media: PTIInspectionMedia[]; }> = {};
    const itemLabel = (id: number | null): string => {
      if (id == null) return 'General';
      return inspection.items?.find(i => i.id === id)?.label ?? `Item #${id}`;
    };
    for (const m of media) {
      const key: GroupKey = m.item_id == null ? 'general' : String(m.item_id);
      if (!groups[key]) {
        groups[key] = { label: itemLabel(m.item_id), media: [] };
      }
      groups[key].media.push(m);
    }
    return groups;
  }, [media, inspection.items]);

  if (media.length === 0) {
    return <p className="text-sm text-muted-foreground">No photos or videos attached.</p>;
  }

  const flatMedia = media;  // for lightbox prev/next
  const active = lightboxIdx != null ? flatMedia[lightboxIdx] : null;

  return (
    <>
      {Object.entries(grouped).map(([key, group]) => (
        <div key={key} className="mb-5">
          <h3 className="text-sm font-medium text-muted-foreground mb-2">
            {group.label}
            <span className="ml-2 text-xs">· {group.media.length}</span>
          </h3>
          <div className="grid grid-cols-3 gap-2">
            {group.media.map((m) => {
              const idx = flatMedia.indexOf(m);
              const src = `/api/inspections/${inspection.id}/media/${m.id}`;
              return (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setLightboxIdx(idx)}
                  className="relative aspect-square overflow-hidden bg-muted rounded-md hover:opacity-80"
                  aria-label={`Open ${m.file_name}`}
                >
                  {m.media_type === 'document' && (m.content_type || '').includes('pdf') ? (
                    <div className="w-full h-full flex flex-col items-center justify-center gap-1 text-muted-foreground p-2">
                      <FileText className="size-6" aria-hidden />
                      <span className="text-2xs text-center break-all line-clamp-2">{m.file_name}</span>
                    </div>
                  ) : m.media_type === 'video' ? (
                    <ThumbnailVideo src={src} alt={m.file_name} />
                  ) : (
                    <ThumbnailImage src={src} alt={m.file_name} />
                  )}
                  {m.media_type === 'video' && (
                    <span className="absolute bottom-1 right-1 bg-black/60 text-white text-2xs px-1.5 py-0.5 rounded">
                      ▶ video
                    </span>
                  )}
                  {m.media_type === 'document' && (
                    <span className="absolute bottom-1 right-1 bg-foreground/70 text-background text-2xs px-1.5 py-0.5 rounded">
                      doc
                    </span>
                  )}
                  {m.annotated_at && (
                    <Badge
                      tone="info"
                      className="absolute top-1 right-1 text-2xs font-semibold"
                      aria-label={`Annotated by driver on ${formatDate(m.annotated_at, { timeZone: tz })}`}
                    >
                      <Pencil className="size-2.5" aria-hidden /> Annotated
                    </Badge>
                  )}
                  {(() => {
                    const v = parseVerdict(m);
                    if (!v) return null;
                    return (
                      <span
                        className={`absolute bottom-1 left-1 text-white text-2xs font-bold w-5 h-5 rounded-full flex items-center justify-center ${VERDICT_DOT_BG[verdictTone(v.verdict)]}`}
                        title={`AI: ${v.summary || v.verdict}`}
                      >
                        {VERDICT_EMOJI[v.verdict]}
                      </span>
                    );
                  })()}
                  {/* Storage state badge — shown only when state is
                      worth flagging.  'remote' is the healthy default
                      so we don't clutter the gallery with a ☁ on
                      every thumb; 'local' / 'syncing' / 'stuck' get
                      visible badges so the reviewer instantly sees a
                      file's tier. */}
                  {(() => {
                    const s = (m.storage_state || 'remote').toLowerCase();
                    if (s === 'remote') return null;
                    const ICON: Record<string, { glyph: string; bg: string; label: string }> = {
                      local:   { glyph: '💾', bg: 'bg-muted-foreground', label: 'Stored locally · sync pending' },
                      syncing: { glyph: '🔄', bg: 'bg-info',             label: 'Uploading to Google Drive…' },
                      stuck:   { glyph: '⚠',  bg: 'bg-danger',           label: 'Sync blocked — reconnect Drive' },
                    };
                    const cfg = ICON[s];
                    if (!cfg) return null;
                    return (
                      <span
                        className={`absolute top-1 left-1 ${cfg.bg} text-white text-2xs font-semibold px-1.5 py-0.5 rounded shadow-sm`}
                        title={cfg.label}
                      >
                        {cfg.glyph}
                      </span>
                    );
                  })()}
                </button>
              );
            })}
          </div>
        </div>
      ))}

      {/* Lightbox — a <Dialog>, though it does not look like one.
          The hand-rolled version had NO Escape handler at all: opening a
          photo trapped a keyboard user, whose only exit was clicking the
          ✕ with a mouse.  It also had no focus trap and no background
          scroll lock.
          The look is unchanged: the CONTENT is the full-bleed black
          surface here, so the primitive's own faint overlay sits behind
          it and never shows.  Geometry goes in inline STYLE because the
          popup's own ``top-1/2 left-1/2 -translate-*`` centring and
          ``max-w-*`` are classes tailwind-merge files under different
          keys than the overrides — the winner would be decided by CSS
          emit order (same trap as the mobile nav's width). */}
      {active && lightboxIdx != null && (
        <Dialog open onOpenChange={(o) => { if (!o) setLightboxIdx(null); }}>
        <DialogContent
          aria-label={active.file_name}
          showCloseButton={false}
          className="bg-black/90 rounded-none ring-0 p-0 gap-0 flex items-center justify-center z-[60]"
          style={{
            inset: 0, top: 0, left: 0, width: '100vw', height: '100dvh',
            maxWidth: 'none', maxHeight: 'none', transform: 'none',
          }}
          onClick={() => setLightboxIdx(null)}
        >
          <DialogTitle className="sr-only">{active.file_name}</DialogTitle>
          <button
            onClick={() => setLightboxIdx(null)}
            aria-label="Close"
            className="absolute top-4 right-4 text-white hover:text-white/80 p-2"
          >
            <X className="size-6" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); downloadMedia(inspection.id, active); }}
            aria-label="Download"
            className="absolute top-4 right-16 text-white hover:text-white/80 p-2"
          >
            <Download className="size-5" />
          </button>
          <div className="max-w-[95vw] max-h-[90vh]" onClick={e => e.stopPropagation()}>
            {active.media_type === 'photo' ? (
              <img
                src={`/api/inspections/${inspection.id}/media/${active.id}`}
                alt={active.file_name}
                className="max-w-[95vw] max-h-[90vh] object-contain"
              />
            ) : (
              <video
                src={`/api/inspections/${inspection.id}/media/${active.id}`}
                controls
                autoPlay
                className="max-w-[95vw] max-h-[90vh] bg-black"
              />
            )}
            <p className="text-white/80 text-xs text-center mt-2">
              {active.file_name}
            </p>
          </div>
        </DialogContent>
        </Dialog>
      )}
    </>
  );
}
