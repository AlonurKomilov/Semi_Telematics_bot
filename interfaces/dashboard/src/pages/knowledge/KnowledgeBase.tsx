import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen, Plus, Search as SearchIcon, X, Pencil, Trash2,
  Check, AlertTriangle, Pin, FileText, FileVideo, FileImage,
  Link as LinkIcon, ChevronDown, ChevronUp,
} from 'lucide-react';
import { apiJSON } from '../../api/client';
import { useAuth } from '../../context/AuthContext';
import {
  PageHeader,
  EmptyState,
  ErrorState,
} from '../../components/shell';

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
  // ``user.telegram_id`` could be returned as string or number depending on
  // the auth provider — coerce to Number so the equality check against the
  // DB BIGINT ``created_by`` is type-safe.
  const myTelegramId = Number(user?.telegram_id || 0);

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
  const [fPinned, setFPinned] = useState(false);

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
    setFMediaType('link'); setFTags(''); setFVisibility('private'); setFTargetRole('all'); setFPinned(false);
    setEditing(null); setShowForm(false);
    setError('');
  };

  const openEdit = (a: KBArticle) => {
    setEditing(a);
    setFTitle(a.title);
    setFDesc(a.description || '');
    setFCategory(a.category);
    setFMediaUrl(a.media_url);
    setFMediaType(a.media_type);
    setFTags(a.tags);
    setFVisibility(a.visibility);
    setFTargetRole(a.target_role || 'all');
    setFPinned(!!a.pinned);
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
        pinned: fPinned,
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

  // B7 fix: split pinned vs. non-pinned visually so they don't blend.
  const { pinned: pinnedArticles, other: otherArticles } = useMemo(() => {
    const pinned: KBArticle[] = [];
    const other: KBArticle[] = [];
    for (const a of articles) (a.pinned ? pinned : other).push(a);
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
              <Plus size={13} />
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
                  onChange={(e) => setFMediaType(e.target.value)}
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
                <label className="block text-xs text-muted-foreground mb-1">
                  {t('knowledge.field_media_url')}
                </label>
                <input
                  type="url"
                  value={fMediaUrl}
                  onChange={(e) => setFMediaUrl(e.target.value)}
                  maxLength={2048}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  placeholder={t('knowledge.field_media_url_placeholder')}
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

            {/* B10 fix: pin checkbox lives in ONE place — below the row,
                always visible, doesn't double-render across the
                private/public branches. */}
            <div>
              <label className="inline-flex items-center gap-2 text-sm text-foreground/80 cursor-pointer">
                <input
                  type="checkbox"
                  checked={fPinned}
                  onChange={(e) => setFPinned(e.target.checked)}
                  className="rounded border-border"
                />
                <Pin size={13} />
                {t('knowledge.field_pin_label')}
              </label>
            </div>

            {fVisibility === 'public' && (
              <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-sm text-yellow-700 dark:text-yellow-400 inline-flex items-start gap-2">
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
                <Plus size={13} />
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
              myTelegramId={myTelegramId}
              canApprove={canApprove}
              getCatLabel={getCatLabel}
              mediaLinkLabel={mediaLinkLabel}
              MediaIcon={MediaIcon}
              onEdit={openEdit}
              onDelete={handleDelete}
              onApprove={handleApprove}
              onReject={handleReject}
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
              myTelegramId={myTelegramId}
              canApprove={canApprove}
              getCatLabel={getCatLabel}
              mediaLinkLabel={mediaLinkLabel}
              MediaIcon={MediaIcon}
              onEdit={openEdit}
              onDelete={handleDelete}
              onApprove={handleApprove}
              onReject={handleReject}
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
  myTelegramId: number;
  canApprove: boolean;
  getCatLabel: (k: string) => string;
  mediaLinkLabel: (t: string) => string;
  MediaIcon: React.FC<{ type: string; size?: number }>;
  onEdit: (a: KBArticle) => void;
  onDelete: (id: number) => void;
  onApprove: (id: number) => void;
  onReject: (id: number) => void;
  t: TFunction;
}

function ArticleSection({
  title, articles, expanded, setExpanded, myTelegramId, canApprove,
  getCatLabel, mediaLinkLabel, MediaIcon, onEdit, onDelete, onApprove,
  onReject, t,
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
            isOwner={Number(a.created_by) === myTelegramId}
            canApprove={canApprove}
            getCatLabel={getCatLabel}
            mediaLinkLabel={mediaLinkLabel}
            MediaIcon={MediaIcon}
            onEdit={onEdit}
            onDelete={onDelete}
            onApprove={onApprove}
            onReject={onReject}
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
  t: TFunction;
}

function ArticleCard({
  article: a, expanded, onToggle, isOwner, canApprove,
  getCatLabel, mediaLinkLabel, MediaIcon, onEdit, onDelete,
  onApprove, onReject, t,
}: CardProps) {
  return (
    <div className="bg-card border border-border rounded-xl overflow-hidden hover:border-border transition-colors">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-4 text-left"
      >
        <span className="shrink-0 text-muted-foreground">
          <MediaIcon type={a.media_type} size={16} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            {!!a.pinned && <Pin size={12} className="text-amber-600 dark:text-amber-400 shrink-0" />}
            <span className="font-medium text-foreground truncate">{a.title}</span>
          </div>
          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
            <span>{getCatLabel(a.category)}</span>
            {a.visibility === 'public' ? (
              a.approved ? (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-green-500/10 border border-green-500/30 rounded text-green-700 dark:text-green-400">
                  {t('knowledge.chip_public')}{a.target_role && a.target_role !== 'all' ? ` · ${a.target_role}` : ''}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-yellow-500/10 border border-yellow-500/30 rounded text-yellow-700 dark:text-yellow-400">
                  {t('knowledge.chip_pending')}
                </span>
              )
            ) : (
              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-muted border border-border rounded text-muted-foreground">
                {t('knowledge.chip_private')}
              </span>
            )}
            <span>{new Date(a.created_at).toLocaleDateString()}</span>
            {a.creator_name && (
              <span>{t('knowledge.by_creator', { name: a.creator_name })}</span>
            )}
          </div>
        </div>
        <span className="text-muted-foreground shrink-0">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </button>

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
          href={a.media_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-2 px-3 py-1.5 bg-primary/15 border border-primary/30 rounded-lg text-sm text-primary hover:bg-primary/25 transition-colors"
        >
          <MediaIcon type={a.media_type} size={13} />
          {mediaLinkLabel(a.media_type)}
        </a>
      )}
      {isOwner && (
        <div className="flex gap-2 pt-2">
          <button
            onClick={() => onEdit(a)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-muted hover:bg-muted/80 text-foreground/80 text-xs rounded transition-colors"
          >
            <Pencil size={11} />
            {t('knowledge.btn_edit')}
          </button>
          <button
            onClick={() => onDelete(a.id)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-destructive/10 hover:bg-destructive/20 border border-destructive/30 text-destructive text-xs rounded transition-colors"
          >
            <Trash2 size={11} />
            {t('knowledge.btn_delete')}
          </button>
        </div>
      )}
      {canApprove && a.visibility === 'public' && !a.approved && (
        <div className="flex gap-2 pt-2 border-t border-border mt-2 flex-wrap items-center">
          <span className="text-xs text-yellow-600 dark:text-yellow-400 self-center mr-2 inline-flex items-center gap-1">
            <AlertTriangle size={11} />
            {t('knowledge.needs_approval_label')}
          </span>
          <button
            onClick={() => onApprove(a.id)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-green-500/10 hover:bg-green-500/20 border border-green-500/30 text-green-700 dark:text-green-400 text-xs rounded transition-colors"
          >
            <Check size={11} />
            {t('knowledge.btn_approve')}
          </button>
          <button
            onClick={() => onReject(a.id)}
            className="inline-flex items-center gap-1 px-3 py-1 bg-destructive/10 hover:bg-destructive/20 border border-destructive/30 text-destructive text-xs rounded transition-colors"
          >
            <X size={11} />
            {t('knowledge.btn_reject')}
          </button>
        </div>
      )}
    </div>
  );
}
