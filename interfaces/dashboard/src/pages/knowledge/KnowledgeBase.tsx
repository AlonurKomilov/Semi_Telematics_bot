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
import { apiFetch, apiJSON } from '../../api/client';
import { toneClasses } from '../../lib/status';
import { useAuth } from '../../context/AuthContext';
import {
  PageHeader,
  EmptyState,
  ErrorState,
} from '../../components/shell';
import { formatRelative } from '../../utils/datetime';

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

  // Create / Edit form
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<KBArticle | null>(null);
  const [saving, setSaving] = useState(false);
  const [fTitle, setFTitle] = useState('');
  const [fDesc, setFDesc] = useState('');
  const [fCategory, setFCategory] = useState('general');
  const [fMediaUrl, setFMediaUrl] = useState('');
  const [fMediaType, setFMediaType] = useState('link');
  const [fTags, setFTags] = useState('');
  const [fVisibility, setFVisibility] = useState('private');
  const [fTargetRole, setFTargetRole] = useState('all');
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

  // Bug B6 fix: every mutation invalidates BOTH articles AND categories
  // so the sidebar counts stay in sync with the list.
  const load = () => {
    qc.invalidateQueries({ queryKey: ['kb-articles'] });
    qc.invalidateQueries({ queryKey: ['kb-categories'] });
  };

  const resetForm = () => {
    setFTitle(''); setFDesc(''); setFCategory('general'); setFMediaUrl('');
    setFMediaType('link'); setFTags(''); setFVisibility('private'); setFTargetRole('all');
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
    setFTargetRole(a.target_role || 'all');
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
        target_role: fVisibility === 'public' ? fTargetRole : 'all',
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
        <select
          value={catFilter}
          onChange={(e) => { setCatFilter(e.target.value); setPage(0); }}
          className="px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
        >
          <option value="">{t('knowledge.filter_all_categories')}</option>
          {categories.map((c) => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>

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
            className="inline-flex items-center justify-center px-3 py-2 bg-muted hover:bg-muted/80 text-foreground text-sm rounded-lg transition-colors"
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
                  placeholder={t('knowledge.field_title_placeholder')}
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_category')}
                </label>
                <select
                  value={fCategory}
                  onChange={(e) => setFCategory(e.target.value)}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                >
                  {categories.map((c) => (
                    <option key={c.key} value={c.key}>{c.label}</option>
                  ))}
                </select>
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
                <select
                  value={fMediaType}
                  onChange={(e) => {
                    // Switching media type invalidates whatever the user
                    // already entered.  A YouTube URL doesn't make sense
                    // as "Image", and a PDF upload from a previous pick
                    // shouldn't carry over to "Link".  Clearing here
                    // beats showing a stale value next to the new type.
                    setFMediaType(e.target.value);
                    setFMediaUrl('');
                    setFUploadName('');
                    setFUploadSize(0);
                  }}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                >
                  <option value="video">{t('knowledge.media_video')}</option>
                  <option value="pdf">{t('knowledge.media_pdf')}</option>
                  <option value="image">{t('knowledge.media_image')}</option>
                  <option value="link">{t('knowledge.media_link')}</option>
                  <option value="none">{t('knowledge.media_none')}</option>
                </select>
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
                  placeholder={t('knowledge.field_tags_placeholder')}
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_visibility')}
                </label>
                <select
                  value={fVisibility}
                  onChange={(e) => setFVisibility(e.target.value)}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                >
                  <option value="private">{t('knowledge.visibility_private')}</option>
                  <option value="public">{t('knowledge.visibility_public')}</option>
                </select>
              </div>
              {fVisibility === 'public' && (
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">
                    {t('knowledge.field_target_role')}
                  </label>
                  <select
                    value={fTargetRole}
                    onChange={(e) => setFTargetRole(e.target.value)}
                    className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  >
                    <option value="all">{t('knowledge.target_all')}</option>
                    <option value="owner">{t('knowledge.target_owner')}</option>
                    <option value="admin">{t('knowledge.target_admin')}</option>
                    <option value="fleet">{t('knowledge.target_fleet')}</option>
                    <option value="safety">{t('knowledge.target_safety')}</option>
                    <option value="dispatcher">{t('knowledge.target_dispatcher')}</option>
                    <option value="driver">{t('knowledge.target_driver')}</option>
                  </select>
                </div>
              )}
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
                className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground text-sm font-medium rounded-lg transition-colors"
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
                className="px-4 py-2 bg-muted hover:bg-muted/80 text-foreground/80 text-sm rounded-lg transition-colors"
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
        <h3 className="text-xs uppercase tracking-wide text-muted-foreground font-semibold inline-flex items-center gap-1.5">
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
  const bookmarked = Boolean(a.is_bookmarked);
  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden hover:border-border transition-colors">
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
                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${toneClasses('ok')}`}>
                  {t('knowledge.chip_public')}{a.target_role && a.target_role !== 'all' ? ` · ${a.target_role}` : ''}
                </span>
              ) : (
                <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded ${toneClasses('warn')}`}>
                  {t('knowledge.chip_pending')}
                </span>
              )
            ) : (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-muted border border-border rounded text-muted-foreground">
                {t('knowledge.chip_private')}
              </span>
            )}
            <span title={new Date(a.created_at).toLocaleString()}>
              {formatRelative(a.created_at)}
            </span>
            {a.updated_at && a.updated_at !== a.created_at && (
              <span
                title={new Date(a.updated_at).toLocaleString()}
                className="opacity-75"
              >
                {t('knowledge.edited_at', 'edited')} {formatRelative(a.updated_at)}
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
          className={`shrink-0 p-1.5 rounded transition-colors ${
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
    </div>
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
                className="px-2 py-0.5 bg-muted border border-border rounded-full text-xs text-muted-foreground"
              >
                #{cleaned}
              </span>
            );
          })}
        </div>
      )}
      {a.media_url && (
        <a
          href={
            a.media_url.startsWith('http://') || a.media_url.startsWith('https://')
              ? a.media_url
              : `/api/knowledge/articles/${a.id}/file`
          }
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-3 py-1.5 bg-primary/15 border border-primary/30 rounded-lg text-sm text-primary hover:bg-primary/25 transition-colors"
        >
          <MediaIcon type={a.media_type} size={14} />
          {mediaLinkLabel(a.media_type)}
        </a>
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
            className={`inline-flex items-center gap-1 px-3 py-1 text-xs rounded transition-colors ${toneClasses('ok')}`}
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
    const accept =
      mediaType === 'pdf'
        ? 'application/pdf'
        : 'image/png,image/jpeg,image/webp';
    const hint =
      mediaType === 'pdf'
        ? t('knowledge.upload_hint_pdf', 'PDF · max 25 MB')
        : t('knowledge.upload_hint_image', 'PNG, JPEG, WEBP · max 25 MB');

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

  // video / link: URL only.
  const urlLabel =
    mediaType === 'video'
      ? t('knowledge.field_video_url', 'Video URL')
      : t('knowledge.field_link_url', 'Link URL');
  const urlPlaceholder =
    mediaType === 'video'
      ? 'https://youtube.com/watch?v=…  ·  https://vimeo.com/…'
      : t('knowledge.field_media_url_placeholder');
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
    </>
  );
}
