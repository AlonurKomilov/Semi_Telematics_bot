import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen, Plus, Search as SearchIcon, X, Pencil, Trash2,
  Check, AlertTriangle, Pin, FileText, FileVideo, FileImage,
  Link as LinkIcon, ChevronDown, ChevronUp,
  ThumbsUp, ThumbsDown, Eye,
  // Category icons
  Wrench, ClipboardCheck, ScrollText, ShieldCheck, Fuel,
  Building2, GraduationCap, Snowflake,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { ContextMenu, type MenuAction } from '../../components/ui/context-menu';

// Lucide icon per category key.  Falls back to BookOpen when the
// backend ships a category the frontend hasn't seen yet (forward-
// compatibility with future additions).
const CATEGORY_ICONS: Record<string, LucideIcon> = {
  maintenance: Wrench,
  fault_codes: AlertTriangle,
  pre_trip:    ClipboardCheck,
  compliance:  ScrollText,
  safety:      ShieldCheck,
  fuel:        Fuel,
  procedures:  Building2,
  training:    GraduationCap,
  reefer:      Snowflake,
  general:     BookOpen,
};

function CategoryIcon({ category, size = 13, className = '' }: { category: string; size?: number; className?: string }) {
  const Icon = CATEGORY_ICONS[category] || BookOpen;
  return <Icon size={size} className={`shrink-0 ${className}`} />;
}

/**
 * Convert a public video / Drive URL into its embeddable iframe src,
 * or null when the URL isn't from a provider we know how to embed.
 *
 * Pattern-matches the four hosts our backend allowlist already accepts
 * — YouTube, Vimeo, Loom, Google Drive — so the operator pastes the
 * normal share link from those services and the article view renders
 * an inline player instead of a "Open link" button.
 *
 * Returns null on:
 *   - any URL not from one of the four providers
 *   - URLs from those providers but in a shape we can't parse
 *     (e.g. youtube.com/channel/... — that's a channel, not a video)
 *
 * Caller falls back to the link button for null returns; we never
 * render an iframe from an arbitrary URL because that would defeat
 * the backend allowlist (XSS via crafted iframe src).
 */
function getEmbedSrc(rawUrl: string): string | null {
  if (!rawUrl) return null;
  let u: URL;
  try {
    u = new URL(rawUrl);
  } catch {
    return null;
  }
  if (u.protocol !== 'https:') return null;
  const host = u.hostname.toLowerCase();

  // YouTube — handles youtube.com/watch?v=ID, youtu.be/ID, and
  // youtube.com/shorts/ID.  ``embed`` is the canonical iframe URL.
  if (host === 'youtu.be') {
    const id = u.pathname.slice(1).split('/')[0];
    if (id) return `https://www.youtube.com/embed/${encodeURIComponent(id)}`;
  }
  if (host === 'youtube.com' || host.endsWith('.youtube.com')) {
    const v = u.searchParams.get('v');
    if (v) return `https://www.youtube.com/embed/${encodeURIComponent(v)}`;
    const m = u.pathname.match(/^\/(?:embed|shorts)\/([A-Za-z0-9_-]+)/);
    if (m) return `https://www.youtube.com/embed/${encodeURIComponent(m[1])}`;
  }

  // Vimeo — vimeo.com/{ID} or vimeo.com/{user}/{ID} share URLs.
  // ``player.vimeo.com/video/{ID}`` is the embed origin.
  if (host === 'vimeo.com' || host.endsWith('.vimeo.com')) {
    const segs = u.pathname.split('/').filter(Boolean);
    const id = segs.find(s => /^\d+$/.test(s));
    if (id) return `https://player.vimeo.com/video/${encodeURIComponent(id)}`;
  }

  // Loom — share URLs look like loom.com/share/{ID}.  Replace
  // ``/share/`` with ``/embed/`` to get the iframe variant.
  if (host === 'loom.com' || host.endsWith('.loom.com')) {
    const m = u.pathname.match(/^\/share\/([A-Za-z0-9]+)/);
    if (m) return `https://www.loom.com/embed/${encodeURIComponent(m[1])}`;
    const m2 = u.pathname.match(/^\/embed\/([A-Za-z0-9]+)/);
    if (m2) return `https://www.loom.com/embed/${encodeURIComponent(m2[1])}`;
  }

  // Google Drive — file/d/{ID}/view → file/d/{ID}/preview.
  // Requires the Drive file to be set to "Anyone with the link can
  // view" — if it isn't, the iframe will render an "access denied"
  // screen, which is the right behaviour (the author needs to fix
  // their sharing settings).
  if (host === 'drive.google.com') {
    const m = u.pathname.match(/^\/file\/d\/([A-Za-z0-9_-]+)/);
    if (m) return `https://drive.google.com/file/d/${encodeURIComponent(m[1])}/preview`;
    const id = u.searchParams.get('id');
    if (id) return `https://drive.google.com/file/d/${encodeURIComponent(id)}/preview`;
  }

  return null;
}

// ── Article media renderer ─────────────────────────────────────────
//
// Decides what to render based on (media_type, URL shape):
//
//   video + recognised provider URL  → iframe (YouTube/Vimeo/Loom/Drive)
//   image + external URL             → <img> directly (no auth needed)
//   image + internal upload          → <img> via auth-fetched blob URL
//   pdf   + Drive URL                → iframe to Drive's /preview (handles
//                                      both videos AND PDFs)
//   pdf   + internal upload          → <embed type=pdf> via auth blob
//   anything else                    → "Open link" button fallback
//
// "Internal upload" means the media_url doesn't start with http(s)://
// — those are paths into our object store, served via the authed
// /api/knowledge/articles/{id}/file endpoint.
//
// Pulled out of the article-card body so the conditional tree stays
// readable + so each authed-blob fetch is scoped to ONE article-media
// component (lifecycle pairs cleanly with the article render).

interface ArticleMediaProps {
  article: KBArticle;
  // ``React.ComponentType`` matches both ``FC`` and plain function
  // components (the existing MediaIcon is an FC) — narrower
  // ``(props) => JSX.Element`` would reject FC<>'s ReactNode return.
  MediaIcon: React.ComponentType<{ type: string; size?: number }>;
  mediaLinkLabel: (type: string) => string;
}

function ArticleMedia({ article: a, MediaIcon, mediaLinkLabel }: ArticleMediaProps) {
  const isExternal =
    a.media_url.startsWith('http://') || a.media_url.startsWith('https://');
  // Authed blob URL — only created when we actually need it (internal
  // upload AND a media type that supports inline rendering).  Empty
  // string skips the fetch.  The hook revokes on unmount automatically.
  const internalFileUrl =
    !isExternal && (a.media_type === 'image' || a.media_type === 'pdf')
      ? `/api/knowledge/articles/${a.id}/file`
      : '';
  const blobUrl = useAuthedBlobUrl(internalFileUrl || null);

  // VIDEO from a known provider (YouTube/Vimeo/Loom/Drive) → iframe.
  // External-only by design; we don't host raw video.
  const embedSrc = isExternal ? getEmbedSrc(a.media_url) : null;
  if (embedSrc) {
    return (
      <div className="rounded-lg overflow-hidden border border-border bg-muted aspect-video">
        <iframe
          src={embedSrc}
          title={a.title || 'Embedded media'}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          referrerPolicy="strict-origin-when-cross-origin"
          className="w-full h-full border-0"
        />
      </div>
    );
  }

  // IMAGE — inline <img> for both external + internal sources.
  // ``loading="lazy"`` defers off-screen images so a long article list
  // doesn't hammer the API with parallel blob fetches at first paint.
  if (a.media_type === 'image') {
    if (isExternal) {
      return (
        <img
          src={a.media_url}
          alt={a.title || 'Article image'}
          loading="lazy"
          className="rounded-lg border border-border bg-muted max-h-96 w-auto object-contain"
        />
      );
    }
    if (blobUrl) {
      return (
        <img
          src={blobUrl}
          alt={a.title || 'Article image'}
          loading="lazy"
          className="rounded-lg border border-border bg-muted max-h-96 w-auto object-contain"
        />
      );
    }
    // Blob still loading — show a thin skeleton so layout doesn't jump.
    return (
      <div className="rounded-lg border border-border bg-muted/50 h-48 animate-pulse" />
    );
  }

  // PDF — inline <embed> for internal uploads (browsers render the
  // native PDF viewer).  External PDFs go through the link button
  // unless they're hosted on Drive (whose /preview URL embeds fine);
  // direct ``*.pdf`` links on Dropbox/S3 typically block iframe
  // embedding via X-Frame-Options, so the link button is the safer
  // default there.
  if (a.media_type === 'pdf') {
    if (!isExternal && blobUrl) {
      return (
        <embed
          src={blobUrl}
          type="application/pdf"
          className="w-full h-96 rounded-lg border border-border bg-muted"
        />
      );
    }
    if (!isExternal) {
      // Still loading the blob — same skeleton shape as image so the
      // layout reserves vertical space upfront.
      return (
        <div className="rounded-lg border border-border bg-muted/50 h-96 animate-pulse" />
      );
    }
    // External PDF + not on Drive — link button.
    // (The Drive case was already handled above by the ``embedSrc``
    // branch since getEmbedSrc recognises drive.google.com regardless
    // of whether the file inside is a video or a PDF.)
  }

  // Fallback — anything we don't have an inline renderer for opens in
  // a new tab.  Covers unknown external hosts, internal Link-type
  // articles, etc.
  const href = isExternal ? a.media_url : `/api/knowledge/articles/${a.id}/file`;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-2 px-3 py-1.5 bg-primary/15 border border-primary/30 rounded-lg text-sm text-primary hover:bg-primary/25 transition-colors"
    >
      <MediaIcon type={a.media_type} size={14} />
      {mediaLinkLabel(a.media_type)}
    </a>
  );
}

// ── Role-aware new-article placeholders ────────────────────────────
//
// The original placeholders ("How to check battery voltage on
// Freightliner" + "freightliner, battery, electrical") were Fleet-
// specific and read as broken for HR / Recruiter / Accounting
// authors.  This map seeds the form with examples + a sensible
// default category that actually look like THAT role's work, so the
// placeholder does what placeholders are for: teach the user what
// kind of content goes in the field.
//
// Adding a new role: add an entry here.  Missing roles fall through
// to the ``default`` entry — never null, so the form is always usable.

interface RolePlaceholders {
  title: string;
  tags: string;
  category: string;  // key from CATEGORY_ICONS / backend categories list
}

const ROLE_PLACEHOLDERS: Record<string, RolePlaceholders> = {
  fleet: {
    title:    'How to check battery voltage on Freightliner',
    tags:     'freightliner, battery, electrical',
    category: 'maintenance',
  },
  safety: {
    title:    'Incident reporting procedure (DOT recordable)',
    tags:     'incident, reporting, dot',
    category: 'safety',
  },
  dispatcher: {
    title:    'Load assignment SOP for overnight runs',
    tags:     'loads, assignment, dispatch',
    category: 'procedures',
  },
  hr: {
    title:    'Driver onboarding checklist',
    tags:     'onboarding, paperwork, dq-file',
    category: 'compliance',
  },
  accounting: {
    title:    'IFTA quarterly filing — step-by-step',
    tags:     'ifta, taxes, quarterly',
    category: 'compliance',
  },
  recruiter: {
    title:    'Pre-employment screening process (MVR + background)',
    tags:     'screening, mvr, background',
    category: 'compliance',
  },
  driver: {
    title:    'How to do a pre-trip inspection',
    tags:     'pti, inspection, daily',
    category: 'pre_trip',
  },
  owner: {
    title:    'Company-wide policy update',
    tags:     'policy, company-wide, announcement',
    category: 'procedures',
  },
  admin: {
    title:    'Account setting change — process',
    tags:     'admin, configuration, account',
    category: 'procedures',
  },
};

const DEFAULT_PLACEHOLDERS: RolePlaceholders = {
  title:    'How to ...',
  tags:     'topic, subtopic, related',
  category: 'general',
};

/** Resolve the placeholder set for the current user's role.
 *  Returns ``DEFAULT_PLACEHOLDERS`` for unknown roles so the form
 *  always shows usable hints. */
function placeholdersForRole(role: string | null | undefined): RolePlaceholders {
  if (!role) return DEFAULT_PLACEHOLDERS;
  return ROLE_PLACEHOLDERS[role] ?? DEFAULT_PLACEHOLDERS;
}

import { apiFetch, apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { useAuth } from '../../context/AuthContext';
import { useAuthedBlobUrl } from '../../hooks/useAuthedBlobUrl';
import {
  PageHeader,
  EmptyState,
  ErrorState,
} from '../../components/shell';
import { formatDate, formatRelative } from '../../utils/datetime';
import { useTimezone } from '../../hooks/useTimezone';
import { Select, SelectTrigger, SelectContent, SelectItem, SelectValue } from '../../components/ui/select';

interface KBArticle {
  id: number;
  title: string;
  description?: string;
  category: string;
  media_url: string;
  media_type: string;
  tags: string;
  visibility: string;
  target_role: string;
  pinned: number;
  created_by: number;
  creator_name: string;
  approved: number;
  view_count?: number;
  helpful_count?: number;
  unhelpful_count?: number;
  /** Decoded from the detail endpoint: 1 / 0 / null. */
  my_vote?: number | null;
  /** Per-user bookmark state — server returns it on every list / get
   *  response.  Distinct from the legacy global ``pinned`` column,
   *  which is no longer surfaced in the UI. */
  is_bookmarked?: boolean;
  created_at: string;
  updated_at: string;
}

interface ArticleListResponse {
  articles: KBArticle[];
  count: number;
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

interface Category {
  key: string;
  label: string;
}

interface KbPermissions {
  can_create: boolean;
  can_approve: boolean;
}

const PAGE_SIZE = 50;

export default function KnowledgeBase() {
  const { t } = useTranslation();
  const { user } = useAuth();
  // ``created_by`` on KB articles stores the stable ``users.id`` —
  // comparing against ``user.id`` lets ownership survive a Telegram
  // re-link (changing telegram_id wouldn't change user.id).  Coerce
  // both sides to Number so a string/number drift from the auth
  // context can't hide the user's own articles.
  const myUserId = Number(user?.id || 0);

  const qc = useQueryClient();
  const [error, setError] = useState('');
  const [catFilter, setCatFilter] = useState('');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [page, setPage] = useState(0);

  // Create / Edit form.  Default category seeds from the user's role
  // so an HR user opening the form lands on 'compliance', a Fleet
  // user on 'maintenance', etc. — they can still pick anything from
  // the dropdown, this just removes a click for the common case.
  // Placeholders ride the same role map (see placeholdersForRole).
  const myRolePlaceholders = placeholdersForRole(user?.role);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<KBArticle | null>(null);
  const [saving, setSaving] = useState(false);
  const [fTitle, setFTitle] = useState('');
  const [fDesc, setFDesc] = useState('');
  const [fCategory, setFCategory] = useState(myRolePlaceholders.category);
  const [fMediaUrl, setFMediaUrl] = useState('');
  const [fMediaType, setFMediaType] = useState('link');
  const [fTags, setFTags] = useState('');
  const [fVisibility, setFVisibility] = useState('private');
  // Role-target removed from the form (Tier: role-isolation made
  // automatic).  Backend derives target_role from the author at
  // create-time; the field is preserved on edit.  Leaving the
  // useState shape would invite a future contributor to wire it
  // back up — explicit comment instead.
  // Upload state: when set, the user attached a file (not a URL).  The
  // backend stores the upload's path in media_url just like any other
  // value, so the create payload doesn't need a special field.
  const [fUploadName, setFUploadName] = useState('');
  const [fUploadSize, setFUploadSize] = useState(0);
  const [fUploading, setFUploading] = useState(false);

  // Expanded article
  const [expanded, setExpanded] = useState<number | null>(null);

  // Backend-driven permissions — the single source of truth for what
  // the user can do.  Avoids the "frontend allows the button, backend
  // 403s the request" class of drift.
  const { data: perms } = useQuery<KbPermissions>({
    queryKey: ['kb-permissions'],
    queryFn: () => apiJSON<KbPermissions>('/knowledge/permissions'),
  });
  const canCreate = perms?.can_create ?? false;
  const canApprove = perms?.can_approve ?? false;

  const { data: articlesData, isLoading: articlesLoading, error: articlesErr } = useQuery({
    queryKey: ['kb-articles', catFilter, search, page],
    queryFn: () => {
      const params = new URLSearchParams();
      if (catFilter) params.set('category', catFilter);
      if (search) params.set('search', search);
      params.set('limit', String(PAGE_SIZE));
      params.set('offset', String(page * PAGE_SIZE));
      return apiJSON<ArticleListResponse>(
        '/knowledge/articles?' + params.toString(),
      );
    },
    placeholderData: (prev) => prev,
  });
  const { data: catsData } = useQuery({
    queryKey: ['kb-categories'],
    queryFn: () => apiJSON<{ categories: Category[] }>('/knowledge/categories'),
  });
  const articles = articlesData?.articles ?? [];
  const total = articlesData?.total ?? 0;
  const hasMore = articlesData?.has_more ?? false;
  const categories = catsData?.categories ?? [];
  const loading = articlesLoading;
  const fetchError = articlesErr instanceof Error ? articlesErr.message : '';

  // Category options for the filter (leads with an "all" entry) and the
  // form picker (categories only) — both derived from the same backend list.
  const catFormItems = useMemo(
    () => categories.map((c) => ({ value: c.key, label: c.label })),
    [categories],
  );
  const catFilterItems = useMemo(
    () => [{ value: '', label: t('knowledge.filter_all_categories') }, ...catFormItems],
    [catFormItems, t],
  );
  const mediaTypeItems = [
    { value: 'video', label: t('knowledge.media_video') },
    { value: 'pdf', label: t('knowledge.media_pdf') },
    { value: 'image', label: t('knowledge.media_image') },
    { value: 'link', label: t('knowledge.media_link') },
    { value: 'none', label: t('knowledge.media_none') },
  ];
  const visibilityItems = [
    { value: 'private', label: t('knowledge.visibility_private', 'Private — my team in this company') },
    { value: 'public', label: t('knowledge.visibility_public', 'Public — every user on the platform') },
  ];

  // Bug B6 fix: every mutation invalidates BOTH articles AND categories
  // so the sidebar counts stay in sync with the list.
  const load = () => {
    qc.invalidateQueries({ queryKey: ['kb-articles'] });
    qc.invalidateQueries({ queryKey: ['kb-categories'] });
  };

  const resetForm = () => {
    setFTitle(''); setFDesc(''); setFCategory(myRolePlaceholders.category); setFMediaUrl('');
    setFMediaType('link'); setFTags(''); setFVisibility('private');
    setFUploadName(''); setFUploadSize(0); setFUploading(false);
    setEditing(null); setShowForm(false);
    setError('');
  };

  const handleUpload = async (file: File) => {
    setError('');
    if (file.size > 25 * 1024 * 1024) {
      setError(t('knowledge.upload_too_large', 'File exceeds the 25 MB limit.'));
      return;
    }
    setFUploading(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      const res = await apiFetch('/knowledge/upload', { method: 'POST', body: fd });
      if (!res.ok) {
        let detail = `Upload failed (${res.status})`;
        try {
          const j = await res.json() as { detail?: string };
          if (j.detail) detail = j.detail;
        } catch { /* keep generic */ }
        throw new Error(detail);
      }
      const r = await res.json() as {
        file_path: string; file_name: string; file_size: number; media_type: string;
      };
      // Wire the upload result into the existing form state: media_url
      // carries the storage path, media_type follows the inferred kind.
      setFMediaUrl(r.file_path);
      setFMediaType(r.media_type);
      setFUploadName(r.file_name);
      setFUploadSize(r.file_size);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed');
    } finally {
      setFUploading(false);
    }
  };

  const clearUpload = () => {
    setFMediaUrl('');
    setFMediaType('link');
    setFUploadName('');
    setFUploadSize(0);
  };

  const openEdit = (a: KBArticle) => {
    setEditing(a);
    setFTitle(a.title);
    setFDesc(a.description || '');
    setFCategory(a.category);
    setFMediaUrl(a.media_url);
    setFMediaType(a.media_type);
    // Restore upload-chip state if the existing media_url is an internal
    // upload path rather than an external URL.  Path-shape detection
    // mirrors the backend's _is_internal_kb_path.
    if (
      a.media_url
      && !a.media_url.startsWith('http://')
      && !a.media_url.startsWith('https://')
    ) {
      const last = a.media_url.split('/').pop() || 'attached file';
      setFUploadName(last);
      setFUploadSize(0);
    } else {
      setFUploadName('');
      setFUploadSize(0);
    }
    setFTags(a.tags);
    setFVisibility(a.visibility);
    // target_role no longer settable from the form — preserved
    // server-side on edit so we don't need to track it in state.
    setShowForm(true);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const body = {
        title: fTitle,
        description: fDesc,
        category: fCategory,
        media_url: fMediaUrl,
        media_type: fMediaType,
        tags: fTags,
        visibility: fVisibility,
        // target_role intentionally NOT sent — backend derives it
        // from the author's role on create (Fleet → fleet, HR → hr,
        // Owner/Admin → 'all') and preserves it on edit.
      };
      if (editing) {
        await apiJSON(`/knowledge/articles/${editing.id}`, { method: 'PUT', body });
      } else {
        await apiJSON('/knowledge/articles', { method: 'POST', body });
      }
      resetForm();
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('knowledge.toast_save_failed'));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm(t('knowledge.confirm_delete'))) return;
    try {
      await apiJSON(`/knowledge/articles/${id}`, { method: 'DELETE' });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('knowledge.toast_delete_failed'));
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
    setPage(0);
  };

  const handleApprove = async (id: number) => {
    try {
      await apiJSON(`/knowledge/articles/${id}/approve`, { method: 'POST' });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('knowledge.toast_approve_failed'));
    }
  };

  const handleReject = async (id: number) => {
    if (!confirm(t('knowledge.confirm_reject'))) return;
    try {
      await apiJSON(`/knowledge/articles/${id}/reject`, { method: 'POST' });
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('knowledge.toast_reject_failed'));
    }
  };

  // Per-user bookmark toggle.  Optimistic — the row reorders before
  // the server confirms because list-sorting bookmarked-first is what
  // the user expects to see immediately.  Failure path snaps back.
  const handleToggleBookmark = async (id: number, currentlyBookmarked: boolean) => {
    qc.setQueryData<ArticleListResponse>(
      ['kb-articles', catFilter, search, page],
      (prev) => prev && {
        ...prev,
        articles: [...prev.articles]
          .map((a) => a.id === id ? { ...a, is_bookmarked: !currentlyBookmarked } : a)
          .sort((a, b) => Number(b.is_bookmarked ?? 0) - Number(a.is_bookmarked ?? 0)),
      },
    );
    try {
      await apiJSON(`/knowledge/articles/${id}/bookmark`, {
        method: currentlyBookmarked ? 'DELETE' : 'POST',
      });
    } catch (e) {
      // Rollback: re-fetch since our optimistic state is stale.
      load();
      setError(e instanceof Error ? e.message : 'Failed to update bookmark');
    }
  };

  const getCatLabel = (key: string) => {
    const c = categories.find(cat => cat.key === key);
    return c?.label || key;
  };

  const MediaIcon = ({ type, size = 14 }: { type: string; size?: number }) => {
    if (type === 'video') return <FileVideo size={size} />;
    if (type === 'pdf') return <FileText size={size} />;
    if (type === 'image') return <FileImage size={size} />;
    if (type === 'link') return <LinkIcon size={size} />;
    return <FileText size={size} />;
  };

  const mediaLinkLabel = (type: string) => {
    switch (type) {
      case 'video': return t('knowledge.btn_watch_video');
      case 'pdf':   return t('knowledge.btn_open_pdf');
      case 'image': return t('knowledge.btn_view_image');
      default:      return t('knowledge.btn_open_link');
    }
  };

  // Split bookmarked-by-me vs the rest so the user's personal pins
  // get their own visually distinct section.  Replaces the older
  // global-pinned split — pinning is now a per-user concept.
  const { pinned: pinnedArticles, other: otherArticles } = useMemo(() => {
    const pinned: KBArticle[] = [];
    const other: KBArticle[] = [];
    for (const a of articles) (a.is_bookmarked ? pinned : other).push(a);
    return { pinned, other };
  }, [articles]);

  const articleCountLabel = total === 1
    ? t('knowledge.article_count_one', { count: total })
    : t('knowledge.article_count_other', { count: total });

  return (
    <div className="space-y-6">
      <PageHeader
        icon={BookOpen}
        title={t('pages.knowledge_title')}
        description={t('pages.knowledge_desc')}
        actions={
          canCreate ? (
            <button
              onClick={() => { resetForm(); setShowForm(true); }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
            >
              <Plus size={14} />
              {t('knowledge.new_article')}
            </button>
          ) : undefined
        }
      />

      {(error || fetchError) && (
        <ErrorState
          message={error || fetchError}
          onRetry={() => setError('')}
        />
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <Select
          value={catFilter}
          onValueChange={(v) => { setCatFilter(v); setPage(0); }}
          items={catFilterItems}
        >
          <SelectTrigger aria-label={t('knowledge.field_category')}><SelectValue /></SelectTrigger>
          <SelectContent>
            {catFilterItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
          </SelectContent>
        </Select>

        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            placeholder={t('knowledge.search_placeholder')}
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground placeholder-muted-foreground w-56"
          />
          <button
            type="submit"
            className="inline-flex items-center justify-center px-3 py-2 bg-muted hover:bg-muted/80 text-foreground text-sm rounded-lg transition-colors min-h-tap"
            aria-label={t('knowledge.search_placeholder')}
          >
            <SearchIcon size={14} />
          </button>
          {search && (
            <button
              type="button"
              onClick={() => { setSearch(''); setSearchInput(''); setPage(0); }}
              className="inline-flex items-center gap-1 px-3 py-2 bg-muted hover:bg-muted/80 text-foreground/80 text-sm rounded-lg"
            >
              <X size={12} />
              {t('knowledge.search_clear')}
            </button>
          )}
        </form>

        <span className="text-sm text-muted-foreground ml-auto">
          {articleCountLabel}
        </span>
      </div>

      {/* Create / Edit Form */}
      {showForm && (
        <div className="bg-card border border-border rounded-xl p-6 space-y-4">
          <h2 className="text-lg font-semibold">
            {editing ? t('knowledge.form_title_edit') : t('knowledge.form_title_new')}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_title')} *
                </label>
                <input
                  type="text"
                  value={fTitle}
                  onChange={(e) => setFTitle(e.target.value)}
                  required
                  maxLength={200}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  placeholder={t(
                    `knowledge.field_title_placeholder_${user?.role ?? 'default'}`,
                    myRolePlaceholders.title,
                  )}
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_category')}
                </label>
                <Select value={fCategory} onValueChange={(v) => setFCategory(v)} items={catFormItems}>
                  <SelectTrigger className="w-full" aria-label={t('knowledge.field_category')}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {catFormItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div>
              <label className="block text-xs text-muted-foreground mb-1">
                {t('knowledge.field_description')}
              </label>
              <textarea
                value={fDesc}
                onChange={(e) => setFDesc(e.target.value)}
                rows={4}
                maxLength={20000}
                className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground resize-y"
                placeholder={t('knowledge.field_description_placeholder')}
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_media_type')}
                </label>
                <Select
                  value={fMediaType}
                  onValueChange={(v) => {
                    // Switching media type invalidates whatever the user
                    // already entered.  A YouTube URL doesn't make sense
                    // as "Image", and a PDF upload from a previous pick
                    // shouldn't carry over to "Link".  Clearing here
                    // beats showing a stale value next to the new type.
                    setFMediaType(v);
                    setFMediaUrl('');
                    setFUploadName('');
                    setFUploadSize(0);
                  }}
                  items={mediaTypeItems}
                >
                  <SelectTrigger className="w-full" aria-label={t('knowledge.field_media_type')}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {mediaTypeItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
              <div className="md:col-span-2">
                <MediaInput
                  mediaType={fMediaType}
                  mediaUrl={fMediaUrl}
                  setMediaUrl={setFMediaUrl}
                  uploadName={fUploadName}
                  uploadSize={fUploadSize}
                  uploading={fUploading}
                  onUpload={handleUpload}
                  onClearUpload={clearUpload}
                  t={t}
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_tags')}
                </label>
                <input
                  type="text"
                  value={fTags}
                  onChange={(e) => setFTags(e.target.value)}
                  maxLength={500}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  placeholder={t(
                    `knowledge.field_tags_placeholder_${user?.role ?? 'default'}`,
                    myRolePlaceholders.tags,
                  )}
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_visibility')}
                </label>
                <Select value={fVisibility} onValueChange={(v) => setFVisibility(v)} items={visibilityItems}>
                  <SelectTrigger className="w-full" aria-label={t('knowledge.field_visibility')}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {visibilityItems.map((it) => <SelectItem key={it.value} value={it.value}>{it.label}</SelectItem>)}
                  </SelectContent>
                </Select>
                <p className="text-2xs text-muted-foreground mt-1">
                  {fVisibility === 'private'
                    ? t(
                        'knowledge.visibility_private_hint',
                        'Visible to other members of your role in this company. Owner / Admin always see every private article in the account.',
                      )
                    : t(
                        'knowledge.visibility_public_hint',
                        'Visible to every user on the platform, regardless of role or company.  Public articles need owner / admin approval before they appear.',
                      )}
                </p>
              </div>
              {/* Role-target selector removed — visibility is now a
                  simple Public/Private choice and role-isolation
                  happens automatically based on the author:
                    • Private + team author → team-only
                      (Fleet → Fleet, Safety → Safety, HR → HR, …)
                    • Private + Owner/Admin → all roles in account
                      (management posts are broad by default)
                    • Public → whole platform, no role gate
                  Operators don't pick a target — the backend derives
                  it from the author's role at create-time, which the
                  hint under the Visibility dropdown explains. */}
            </div>

            {/* Pin moved out of the form entirely: pinning is now a
                per-user bookmark (see the Pin button on every article
                card).  Every reader can curate their own quick-access
                list without affecting anyone else in the company. */}

            {fVisibility === 'public' && (
              <div className={`p-3 rounded-lg text-sm inline-flex items-start gap-2 ${toneClasses('warn')}`}>
                <AlertTriangle size={14} className="shrink-0 mt-0.5" />
                <span>{t('knowledge.public_approval_warning')}</span>
              </div>
            )}

            <div className="flex gap-3">
              <button
                type="submit"
                disabled={saving || !fTitle.trim()}
                className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground text-sm font-medium rounded-lg transition-colors min-h-tap"
              >
                {saving
                  ? t('knowledge.btn_saving')
                  : editing
                    ? t('knowledge.btn_update')
                    : t('knowledge.btn_create')}
              </button>
              <button
                type="button"
                onClick={resetForm}
                className="px-4 py-2 bg-muted hover:bg-muted/80 text-foreground/80 text-sm rounded-lg transition-colors min-h-tap"
              >
                {t('knowledge.btn_cancel')}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Articles list */}
      {loading && !articlesData ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="bg-card border border-border rounded-xl p-5 animate-pulse">
              <div className="h-4 w-1/3 bg-muted rounded mb-3" />
              <div className="h-3 w-2/3 bg-muted rounded mb-2" />
              <div className="h-3 w-1/2 bg-muted rounded" />
            </div>
          ))}
        </div>
      ) : articles.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={search || catFilter ? t('knowledge.empty_no_articles_filter') : t('knowledge.empty_no_articles')}
          description={
            canCreate
              ? t('knowledge.empty_desc_can_create')
              : t('knowledge.empty_desc_read_only')
          }
          action={
            canCreate ? (
              <button
                onClick={() => { resetForm(); setShowForm(true); }}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 transition"
              >
                <Plus size={14} />
                {t('knowledge.new_article')}
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="space-y-6">
          {/* Pinned section */}
          {pinnedArticles.length > 0 && (
            <ArticleSection
              title={t('knowledge.section_pinned')}
              articles={pinnedArticles}
              expanded={expanded}
              setExpanded={setExpanded}
              myUserId={myUserId}
              canApprove={canApprove}
              getCatLabel={getCatLabel}
              mediaLinkLabel={mediaLinkLabel}
              MediaIcon={MediaIcon}
              onEdit={openEdit}
              onDelete={handleDelete}
              onApprove={handleApprove}
              onReject={handleReject}
              onToggleBookmark={handleToggleBookmark}
              t={t}
            />
          )}
          {/* Other articles */}
          {otherArticles.length > 0 && (
            <ArticleSection
              title={pinnedArticles.length > 0 ? t('knowledge.section_other') : undefined}
              articles={otherArticles}
              expanded={expanded}
              setExpanded={setExpanded}
              myUserId={myUserId}
              canApprove={canApprove}
              getCatLabel={getCatLabel}
              mediaLinkLabel={mediaLinkLabel}
              MediaIcon={MediaIcon}
              onEdit={openEdit}
              onDelete={handleDelete}
              onApprove={handleApprove}
              onReject={handleReject}
              onToggleBookmark={handleToggleBookmark}
              t={t}
            />
          )}

          {/* Pagination footer */}
          {(hasMore || page > 0) && (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                {t('knowledge.showing_of', {
                  shown: page * PAGE_SIZE + articles.length,
                  total,
                })}
              </span>
              <div className="flex gap-2">
                {page > 0 && (
                  <button
                    type="button"
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md"
                  >
                    ←
                  </button>
                )}
                {hasMore && (
                  <button
                    type="button"
                    onClick={() => setPage((p) => p + 1)}
                    className="px-3 py-1.5 bg-muted hover:bg-muted/80 rounded-md"
                  >
                    {t('knowledge.load_more')} →
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ── Sub-components ──────────────────────────────────────────────────


interface SectionProps {
  title?: string;
  articles: KBArticle[];
  expanded: number | null;
  setExpanded: (id: number | null) => void;
  myUserId: number;
  canApprove: boolean;
  getCatLabel: (k: string) => string;
  mediaLinkLabel: (t: string) => string;
  MediaIcon: React.FC<{ type: string; size?: number }>;
  onEdit: (a: KBArticle) => void;
  onDelete: (id: number) => void;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  onToggleBookmark: (id: number, currentlyBookmarked: boolean) => void;
  t: TFunction;
}

function ArticleSection({
  title, articles, expanded, setExpanded, myUserId, canApprove,
  getCatLabel, mediaLinkLabel, MediaIcon, onEdit, onDelete, onApprove,
  onReject, onToggleBookmark, t,
}: SectionProps) {
  return (
    <section className="space-y-3">
      {title && (
        <h3 className="text-xs uppercase tracking-wide text-muted-foreground font-medium inline-flex items-center gap-1.5">
          {title === 'Pinned' || title.length < 12
            ? <Pin size={12} />
            : null}
          {title}
        </h3>
      )}
      <div className="space-y-3">
        {articles.map((a) => (
          <ArticleCard
            key={a.id}
            article={a}
            expanded={expanded === a.id}
            onToggle={() => setExpanded(expanded === a.id ? null : a.id)}
            isOwner={Number(a.created_by) === myUserId}
            canApprove={canApprove}
            getCatLabel={getCatLabel}
            mediaLinkLabel={mediaLinkLabel}
            MediaIcon={MediaIcon}
            onEdit={onEdit}
            onDelete={onDelete}
            onApprove={onApprove}
            onReject={onReject}
            onToggleBookmark={onToggleBookmark}
            t={t}
          />
        ))}
      </div>
    </section>
  );
}


interface CardProps {
  article: KBArticle;
  expanded: boolean;
  onToggle: () => void;
  isOwner: boolean;
  canApprove: boolean;
  getCatLabel: (k: string) => string;
  mediaLinkLabel: (t: string) => string;
  MediaIcon: React.FC<{ type: string; size?: number }>;
  onEdit: (a: KBArticle) => void;
  onDelete: (id: number) => void;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  onToggleBookmark: (id: number, currentlyBookmarked: boolean) => void;
  t: TFunction;
}

function ArticleCard({
  article: a, expanded, onToggle, isOwner, canApprove,
  getCatLabel, mediaLinkLabel, MediaIcon, onEdit, onDelete,
  onApprove, onReject, onToggleBookmark, t,
}: CardProps) {
  const tz = useTimezone();
  const bookmarked = Boolean(a.is_bookmarked);
  // Right-click the card → Bookmark, plus Edit / Delete for the owner
  // (the inline controls in the expanded body stay as the visible path).
  const cardMenu: MenuAction[] = [
    { key: 'bookmark', label: bookmarked ? 'Remove bookmark' : 'Bookmark', icon: <Pin size={14} className={bookmarked ? 'text-primary fill-current' : 'text-muted-foreground'} />, onSelect: () => onToggleBookmark(a.id, bookmarked) },
    ...(isOwner ? [
      { key: 'edit', label: 'Edit', icon: <Pencil size={14} className="text-muted-foreground" />, separatorBefore: true, onSelect: () => onEdit(a) },
      { key: 'delete', label: 'Delete', icon: <Trash2 size={14} />, danger: true, onSelect: () => onDelete(a.id) },
    ] : []),
  ];
  return (
    <ContextMenu items={cardMenu} render={<div className="bg-card border border-border rounded-xl overflow-hidden hover:border-border transition-colors" />}>
      {/* Header row.  Native ``<button>`` doesn't allow a nested
          button (the bookmark pin), so we use a div with a click +
          keyboard handler.  The bookmark button stops propagation so
          clicking it doesn't also toggle the expand. */}
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onToggle();
          }
        }}
        className="w-full flex items-center gap-3 p-4 text-left cursor-pointer"
      >
        <span className="shrink-0 text-muted-foreground">
          <MediaIcon type={a.media_type} size={16} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-foreground truncate">{a.title}</span>
          </div>
          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
            <span className="inline-flex items-center gap-1">
              <CategoryIcon category={a.category} />
              {getCatLabel(a.category)}
            </span>
            {a.visibility === 'public' ? (
              a.approved ? (
                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md ${toneClasses('ok')}`}>
                  {t('knowledge.chip_public')}{a.target_role && a.target_role !== 'all' ? ` · ${a.target_role}` : ''}
                </span>
              ) : (
                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md ${toneClasses('warn')}`}>
                  {t('knowledge.chip_pending')}
                </span>
              )
            ) : (
              // Private chip now also surfaces the role scope so a
              // reader knows whether they're looking at a team-only
              // article ("Private · fleet") or an account-wide one
              // ("Private" — the legacy / Owner-Admin default).
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-muted border border-border rounded text-muted-foreground">
                {t('knowledge.chip_private')}
                {a.target_role && a.target_role !== 'all' ? ` · ${a.target_role}` : ''}
              </span>
            )}
            <span title={formatDate(a.created_at, { timeZone: tz })}>
              {formatRelative(a.created_at, { timeZone: tz })}
            </span>
            {a.updated_at && a.updated_at !== a.created_at && (
              <span
                title={formatDate(a.updated_at, { timeZone: tz })}
                className="opacity-75"
              >
                {t('knowledge.edited_at', 'edited')} {formatRelative(a.updated_at, { timeZone: tz })}
              </span>
            )}
            {a.creator_name && (
              <span>{t('knowledge.by_creator', { name: a.creator_name })}</span>
            )}
          </div>
        </div>
        {/* Personal bookmark toggle.  Per-user — clicking changes
            only what THIS user sees in their list; other users in
            the same company are unaffected.  stopPropagation keeps
            the surrounding row from also toggling expand. */}
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggleBookmark(a.id, bookmarked);
          }}
          title={
            bookmarked
              ? t('knowledge.bookmark_remove_title', 'Remove from my bookmarks')
              : t('knowledge.bookmark_add_title', 'Bookmark for myself')
          }
          aria-label={
            bookmarked
              ? t('knowledge.bookmark_remove_title', 'Remove from my bookmarks')
              : t('knowledge.bookmark_add_title', 'Bookmark for myself')
          }
          className={`shrink-0 inline-flex size-7 items-center justify-center rounded-md transition-colors ${
            bookmarked
              ? 'text-amber-600 dark:text-amber-400 hover:bg-amber-500/10'
              : 'text-muted-foreground hover:text-foreground hover:bg-muted'
          }`}
        >
          <Pin size={14} className={bookmarked ? 'fill-current' : ''} />
        </button>
        <span className="text-muted-foreground shrink-0">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </div>

      {expanded && (
        <ExpandedArticleBody
          articleId={a.id}
          summary={a}
          isOwner={isOwner}
          canApprove={canApprove}
          mediaLinkLabel={mediaLinkLabel}
          MediaIcon={MediaIcon}
          onEdit={onEdit}
          onDelete={onDelete}
          onApprove={onApprove}
          onReject={onReject}
          t={t}
        />
      )}
    </ContextMenu>
  );
}


function ExpandedArticleBody({
  articleId, summary, isOwner, canApprove, mediaLinkLabel, MediaIcon,
  onEdit, onDelete, onApprove, onReject, t,
}: {
  articleId: number;
  summary: KBArticle;
  isOwner: boolean;
  canApprove: boolean;
  mediaLinkLabel: (t: string) => string;
  MediaIcon: React.FC<{ type: string; size?: number }>;
  onEdit: (a: KBArticle) => void;
  onDelete: (id: number) => void;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  t: TFunction;
}) {
  // P3 fix continuation: the list endpoint omits description for speed.
  // Fetch the full row on demand when the user expands a card.
  const { data: full } = useQuery<KBArticle>({
    queryKey: ['kb-article', articleId],
    queryFn: () => apiJSON<KBArticle>(`/knowledge/articles/${articleId}`),
    placeholderData: summary,
  });
  const a = full ?? summary;

  // View ping (debounced): fire once per article-open, dedup'd in
  // sessionStorage so collapse/re-expand doesn't double-count.  Failure
  // is silent — view tracking is best-effort.
  const viewPingedRef = useRef(false);
  useEffect(() => {
    if (viewPingedRef.current) return;
    viewPingedRef.current = true;
    const key = `kb-view-${articleId}`;
    const lastSeen = sessionStorage.getItem(key);
    const now = Date.now();
    // 30s debounce per article per browser session.
    if (lastSeen && now - Number(lastSeen) < 30_000) return;
    sessionStorage.setItem(key, String(now));
    apiJSON(`/knowledge/articles/${articleId}/view`, { method: 'POST', body: {} })
      .catch(() => { /* best-effort */ });
  }, [articleId]);
  return (
    <div className="border-t border-border p-4 space-y-3">
      {a.description && (
        <p className="text-sm text-foreground/80 whitespace-pre-wrap">{a.description}</p>
      )}
      {a.tags && (
        <div className="flex flex-wrap gap-1.5">
          {a.tags.split(',').map((tag, i) => {
            const cleaned = tag.trim();
            if (!cleaned) return null;
            return (
              <span
                key={`${i}-${cleaned}`}
                className="px-2 py-0.5 bg-muted border border-border rounded-md text-xs text-muted-foreground"
              >
                #{cleaned}
              </span>
            );
          })}
        </div>
      )}
      {a.media_url && (
        <ArticleMedia
          article={a}
          MediaIcon={MediaIcon}
          mediaLinkLabel={mediaLinkLabel}
        />
      )}

      <ArticleEngagement
        articleId={a.id}
        viewCount={a.view_count ?? 0}
        helpfulCount={a.helpful_count ?? 0}
        unhelpfulCount={a.unhelpful_count ?? 0}
        myVote={a.my_vote ?? null}
        t={t}
      />

      {isOwner && (
        <div className="flex gap-2 pt-2">
          <button
            onClick={() => onEdit(a)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-muted hover:bg-muted/80 text-foreground/80 text-xs rounded transition-colors"
          >
            <Pencil size={12} />
            {t('knowledge.btn_edit')}
          </button>
          <button
            onClick={() => onDelete(a.id)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-destructive/10 hover:bg-destructive/20 border border-destructive/30 text-destructive text-xs rounded transition-colors"
          >
            <Trash2 size={12} />
            {t('knowledge.btn_delete')}
          </button>
        </div>
      )}
      {canApprove && a.visibility === 'public' && !a.approved && (
        <div className="flex gap-2 pt-2 border-t border-border mt-2 flex-wrap items-center">
          <span className="text-xs text-warn self-center mr-2 inline-flex items-center gap-1">
            <AlertTriangle size={12} />
            {t('knowledge.needs_approval_label')}
          </span>
          <button
            onClick={() => onApprove(a.id)}
            className={`inline-flex items-center gap-1 px-3 py-1 text-xs rounded-md transition-colors ${toneClasses('ok')}`}
          >
            <Check size={12} />
            {t('knowledge.btn_approve')}
          </button>
          <button
            onClick={() => onReject(a.id)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-destructive/10 hover:bg-destructive/20 border border-destructive/30 text-destructive text-xs rounded transition-colors"
          >
            <X size={12} />
            {t('knowledge.btn_reject')}
          </button>
        </div>
      )}
    </div>
  );
}


// ── Engagement bar (views + helpful / unhelpful) ────────────────
//
// Voting is optimistic — the chip flips on click and rolls back if the
// API rejects the vote.  Re-clicking the same chip leaves the vote
// unchanged (backend treats it as a no-op).

interface EngagementResp {
  ok: boolean;
  vote: number;
  helpful_count: number;
  unhelpful_count: number;
}

function ArticleEngagement({
  articleId, viewCount, helpfulCount, unhelpfulCount, myVote, t,
}: {
  articleId: number;
  viewCount: number;
  helpfulCount: number;
  unhelpfulCount: number;
  myVote: number | null;
  t: TFunction;
}) {
  const [counts, setCounts] = useState({ helpful: helpfulCount, unhelpful: unhelpfulCount });
  const [vote, setVote] = useState<number | null>(myVote);
  const [busy, setBusy] = useState(false);

  // Sync when the prop-derived initial values change (article refetch).
  useEffect(() => {
    setCounts({ helpful: helpfulCount, unhelpful: unhelpfulCount });
    setVote(myVote);
  }, [articleId, helpfulCount, unhelpfulCount, myVote]);

  const submit = async (helpful: boolean) => {
    if (busy) return;
    const intended = helpful ? 1 : 0;
    if (vote === intended) return;
    // Optimistic: shift counters locally before the round-trip.
    setBusy(true);
    const prevCounts = counts;
    const prevVote = vote;
    setCounts((c) => {
      const next = { ...c };
      if (helpful) {
        next.helpful = c.helpful + 1;
        if (prevVote === 0) next.unhelpful = Math.max(0, c.unhelpful - 1);
      } else {
        next.unhelpful = c.unhelpful + 1;
        if (prevVote === 1) next.helpful = Math.max(0, c.helpful - 1);
      }
      return next;
    });
    setVote(intended);
    try {
      const r = await apiJSON<EngagementResp>(
        `/knowledge/articles/${articleId}/feedback`,
        { method: 'POST', body: { helpful } },
      );
      setCounts({ helpful: r.helpful_count, unhelpful: r.unhelpful_count });
    } catch {
      // Rollback on failure.
      setCounts(prevCounts);
      setVote(prevVote);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center gap-3 pt-2 text-xs text-muted-foreground">
      <span
        title={t('knowledge.views_title', 'Total times this article has been opened')}
        className="inline-flex items-center gap-1"
      >
        <Eye size={12} />
        {viewCount}
      </span>
      <button
        type="button"
        disabled={busy}
        onClick={() => submit(true)}
        title={t('knowledge.helpful_title', 'Mark this article helpful')}
        className={`inline-flex items-center gap-1 px-2 py-1 rounded border transition-colors disabled:opacity-50 ${
          vote === 1
            ? toneClasses('ok')
            : 'border-border hover:bg-muted'
        }`}
      >
        <ThumbsUp size={12} />
        {counts.helpful}
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => submit(false)}
        title={t('knowledge.unhelpful_title', 'Mark this article unhelpful')}
        className={`inline-flex items-center gap-1 px-2 py-1 rounded border transition-colors disabled:opacity-50 ${
          vote === 0
            ? 'bg-destructive/10 border-destructive/40 text-destructive'
            : 'border-border hover:bg-muted'
        }`}
      >
        <ThumbsDown size={12} />
        {counts.unhelpful}
      </button>
    </div>
  );
}


// ── Type-adaptive media input ──────────────────────────────────────
//
// The Media Type select drives which input the user sees underneath
// it:
//   • pdf  → primary file picker, secondary "use URL instead" toggle
//   • image → same as pdf, picker restricted to PNG/JPEG/WEBP
//   • video → URL input (YouTube/Vimeo placeholder).  No uploader —
//             the object store doesn't accept video binaries today.
//   • link  → generic URL input.  No uploader.
//   • none  → nothing rendered.  Article body is the entire content.

function MediaInput({
  mediaType, mediaUrl, setMediaUrl,
  uploadName, uploadSize, uploading,
  onUpload, onClearUpload, t,
}: {
  mediaType: string;
  mediaUrl: string;
  setMediaUrl: (v: string) => void;
  uploadName: string;
  uploadSize: number;
  uploading: boolean;
  onUpload: (f: File) => void;
  onClearUpload: () => void;
  t: TFunction;
}) {
  if (mediaType === 'none') {
    return (
      <div className="text-xs text-muted-foreground italic mt-5">
        {t(
          'knowledge.media_none_hint',
          'No file or link will be attached — readers see only the article body.',
        )}
      </div>
    );
  }

  const isFile = mediaType === 'pdf' || mediaType === 'image';

  // File-type media: picker is the primary affordance, URL is the
  // escape hatch for users who have the file hosted elsewhere (Drive,
  // Dropbox).  Inverts the current order so the upload flow is the
  // discoverable one.
  if (isFile) {
    // GIF added to the image whitelist alongside PNG/JPEG/WEBP so
    // animated step-by-step guides (click here → dialog appears →
    // press save) work without a heavyweight video pipeline.  Real
    // video files (MP4/MOV) are deliberately omitted — the Video
    // type below handles those via YouTube/Vimeo/Loom embeds, which
    // bring captions + adaptive streaming for free.
    const accept =
      mediaType === 'pdf'
        ? 'application/pdf'
        : 'image/png,image/jpeg,image/webp,image/gif';
    const hint =
      mediaType === 'pdf'
        ? t('knowledge.upload_hint_pdf', 'PDF · max 25 MB')
        : t('knowledge.upload_hint_image', 'PNG, JPEG, WEBP, GIF · max 25 MB');

    if (uploadName) {
      return (
        <>
          <label className="block text-xs text-muted-foreground mb-1">
            {mediaType === 'pdf'
              ? t('knowledge.field_pdf', 'PDF file')
              : t('knowledge.field_image', 'Image file')}
          </label>
          <div className="flex items-center gap-2 px-3 py-2 bg-primary/5 border border-primary/30 rounded-lg text-sm">
            <FileText size={14} className="text-primary shrink-0" />
            <span className="truncate flex-1">{uploadName}</span>
            {uploadSize > 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {(uploadSize / 1024).toFixed(0)} KB
              </span>
            )}
            <button
              type="button"
              onClick={onClearUpload}
              className="text-muted-foreground hover:text-destructive"
              title={t('knowledge.upload_remove', 'Remove attached file')}
            >
              <X size={14} />
            </button>
          </div>
        </>
      );
    }
    // Empty: show the picker as the primary affordance + a small URL
    // escape hatch for files hosted elsewhere.
    return (
      <>
        <label className="block text-xs text-muted-foreground mb-1">
          {mediaType === 'pdf'
            ? t('knowledge.field_pdf', 'PDF file')
            : t('knowledge.field_image', 'Image file')}
        </label>
        <label
          className={`flex items-center justify-center gap-2 px-3 py-3 border-2 border-dashed border-border rounded-lg text-sm cursor-pointer hover:bg-muted/30 transition-colors ${
            uploading ? 'opacity-50 pointer-events-none' : ''
          }`}
        >
          <input
            type="file"
            accept={accept}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) onUpload(f);
              e.target.value = '';
            }}
            className="hidden"
            disabled={uploading}
          />
          <FileText size={14} className="text-muted-foreground" />
          <span>
            {uploading
              ? t('knowledge.upload_uploading', 'Uploading…')
              : mediaType === 'pdf'
              ? t('knowledge.upload_pick_pdf', 'Choose a PDF file')
              : t('knowledge.upload_pick_image', 'Choose an image file')}
          </span>
        </label>
        <p className="text-2xs text-muted-foreground mt-1">{hint}</p>
        <div className="mt-3">
          <label className="block text-2xs text-muted-foreground mb-1">
            {t('knowledge.upload_or_url', 'Or paste a hosted URL')}
          </label>
          <input
            type="url"
            value={mediaUrl}
            onChange={(e) => setMediaUrl(e.target.value)}
            maxLength={2048}
            className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
            placeholder={
              mediaType === 'pdf'
                ? 'https://drive.google.com/…  ·  https://dropbox.com/…'
                : 'https://imgur.com/…  ·  https://drive.google.com/…'
            }
          />
        </div>
      </>
    );
  }

  // video / link: URL only.  Video deliberately doesn't accept
  // uploads — hosting platforms (YouTube/Vimeo/Loom) give captions,
  // adaptive streaming, and mobile playback for free, which a 25 MB
  // self-hosted MP4 can't match.  The hint below tells the operator
  // WHY there's no upload button so the missing affordance reads as
  // intentional rather than broken.
  const isVideo = mediaType === 'video';
  const urlLabel = isVideo
    ? t('knowledge.field_video_url', 'Video URL')
    : t('knowledge.field_link_url', 'Link URL');
  const urlPlaceholder = isVideo
    ? 'https://youtube.com/watch?v=…  ·  https://vimeo.com/…  ·  https://loom.com/…'
    : t('knowledge.field_link_url_placeholder', 'https://…');
  const hint = isVideo
    ? t(
        'knowledge.video_hint',
        'Paste a YouTube, Vimeo, or Loom link. Host the video there first — readers get captions and smooth playback on mobile that a direct upload can\'t match.',
      )
    : null;
  return (
    <>
      <label className="block text-xs text-muted-foreground mb-1">
        {urlLabel}
      </label>
      <input
        type="url"
        value={mediaUrl}
        onChange={(e) => setMediaUrl(e.target.value)}
        maxLength={2048}
        className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
        placeholder={urlPlaceholder}
      />
      {hint && (
        <p className="text-2xs text-muted-foreground mt-1">{hint}</p>
      )}
    </>
  );
}
