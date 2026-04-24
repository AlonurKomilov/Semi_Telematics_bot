import { useState, useEffect, useCallback } from 'react';
import { apiJSON } from '../../api/client';
import { usePermissions } from '../../hooks/usePermissions';
import { useAuth } from '../../context/AuthContext';

interface KBArticle {
  id: number;
  title: string;
  description: string;
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

interface Category {
  key: string;
  label: string;
}

const MEDIA_TYPES = [
  { value: 'video', label: '▶️ Video' },
  { value: 'pdf', label: '📄 PDF' },
  { value: 'image', label: '🖼 Image' },
  { value: 'link', label: '🔗 Link' },
  { value: 'none', label: '📝 Text only' },
];

const VISIBILITY_OPTIONS = [
  { value: 'private', label: '🔒 Private (my company only)' },
  { value: 'public', label: '🌐 Public (all companies)' },
];

const TARGET_ROLE_OPTIONS = [
  { value: 'all', label: 'Everyone' },
  { value: 'owner', label: 'Owner only' },
  { value: 'admin', label: 'Admin only' },
  { value: 'fleet', label: 'Fleet only' },
  { value: 'safety', label: 'Safety only' },
  { value: 'dispatcher', label: 'Dispatcher only' },
  { value: 'driver', label: 'Driver only' },
];

export default function KnowledgeBase() {
  const { role } = usePermissions();
  const { user } = useAuth();
  const canManage = ['owner', 'admin', 'fleet', 'safety'].includes(role || '');
  const isAdmin = ['owner', 'admin'].includes(role || '');
  const myTelegramId = user?.telegram_id || 0;

  const [articles, setArticles] = useState<KBArticle[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [catFilter, setCatFilter] = useState('');
  const [search, setSearch] = useState('');
  const [searchInput, setSearchInput] = useState('');

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

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (catFilter) params.set('category', catFilter);
      if (search) params.set('search', search);
      const qs = params.toString() ? '?' + params.toString() : '';
      const [articlesRes, catsRes] = await Promise.all([
        apiJSON<{ articles: KBArticle[] }>('/knowledge/articles' + qs),
        apiJSON<{ categories: Category[] }>('/knowledge/categories'),
      ]);
      setArticles(articlesRes.articles || []);
      setCategories(catsRes.categories || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [catFilter, search]);

  useEffect(() => { load(); }, [load]);

  const resetForm = () => {
    setFTitle(''); setFDesc(''); setFCategory('general'); setFMediaUrl('');
    setFMediaType('link'); setFTags(''); setFVisibility('private'); setFTargetRole('all'); setFPinned(false);
    setEditing(null); setShowForm(false);
  };

  const openEdit = (a: KBArticle) => {
    setEditing(a);
    setFTitle(a.title);
    setFDesc(a.description);
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
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this article?')) return;
    try {
      await apiJSON(`/knowledge/articles/${id}`, { method: 'DELETE' });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    setSearch(searchInput);
  };

  const handleApprove = async (id: number) => {
    try {
      await apiJSON(`/knowledge/articles/${id}/approve`, { method: 'POST' });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approve failed');
    }
  };

  const handleReject = async (id: number) => {
    if (!confirm('Reject and remove this article?')) return;
    try {
      await apiJSON(`/knowledge/articles/${id}/reject`, { method: 'POST' });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Reject failed');
    }
  };

  const getCatLabel = (key: string) => {
    const c = categories.find(cat => cat.key === key);
    return c?.label || key;
  };

  const mediaIcon = (type: string) =>
    ({ video: '▶️', pdf: '📄', image: '🖼', link: '🔗' }[type] || '📝');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">📚 Knowledge Base</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Tips, guides &amp; documentation for your fleet team
          </p>
        </div>
        {canManage && (
          <button
            onClick={() => { resetForm(); setShowForm(true); }}
            className="px-4 py-2 bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-medium rounded-lg transition-colors"
          >
            + New Article
          </button>
        )}
      </div>

      {error && (
        <div className="p-3 bg-destructive/10 border border-destructive/30 rounded-lg text-sm text-destructive">
          {error}
          <button onClick={() => setError('')} className="ml-2 text-destructive/60 hover:text-destructive">✕</button>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <select
          value={catFilter}
          onChange={(e) => setCatFilter(e.target.value)}
          className="px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
        >
          <option value="">All Categories</option>
          {categories.map((c) => (
            <option key={c.key} value={c.key}>{c.label}</option>
          ))}
        </select>

        <form onSubmit={handleSearch} className="flex gap-2">
          <input
            type="text"
            placeholder="Search articles..."
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            className="px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground placeholder-muted-foreground w-56"
          />
          <button
            type="submit"
            className="px-3 py-2 bg-muted hover:bg-muted/80 text-foreground text-sm rounded-lg transition-colors"
          >
            🔍
          </button>
          {search && (
            <button
              type="button"
              onClick={() => { setSearch(''); setSearchInput(''); }}
              className="px-3 py-2 bg-muted hover:bg-muted/80 text-foreground/80 text-sm rounded-lg"
            >
              ✕ Clear
            </button>
          )}
        </form>

        <span className="text-sm text-muted-foreground ml-auto">
          {articles.length} article{articles.length !== 1 ? 's' : ''}
        </span>
      </div>

      {/* Create / Edit Form */}
      {showForm && (
        <div className="bg-card border border-border rounded-xl p-6 space-y-4">
          <h2 className="text-lg font-semibold">
            {editing ? 'Edit Article' : 'New Article'}
          </h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Title *</label>
                <input
                  type="text"
                  value={fTitle}
                  onChange={(e) => setFTitle(e.target.value)}
                  required
                  maxLength={200}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  placeholder="How to check battery voltage on Freightliner"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Category</label>
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
              <label className="block text-xs text-muted-foreground mb-1">Description</label>
              <textarea
                value={fDesc}
                onChange={(e) => setFDesc(e.target.value)}
                rows={4}
                className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground resize-y"
                placeholder="Step-by-step guide, notes, or detailed description..."
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Media Type</label>
                <select
                  value={fMediaType}
                  onChange={(e) => setFMediaType(e.target.value)}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                >
                  {MEDIA_TYPES.map((m) => (
                    <option key={m.value} value={m.value}>{m.label}</option>
                  ))}
                </select>
              </div>
              <div className="md:col-span-2">
                <label className="block text-xs text-muted-foreground mb-1">Media URL</label>
                <input
                  type="url"
                  value={fMediaUrl}
                  onChange={(e) => setFMediaUrl(e.target.value)}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  placeholder="https://youtube.com/watch?v=... or PDF link"
                />
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Tags (comma separated)</label>
                <input
                  type="text"
                  value={fTags}
                  onChange={(e) => setFTags(e.target.value)}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  placeholder="freightliner, battery, electrical"
                />
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-1">Visibility</label>
                <select
                  value={fVisibility}
                  onChange={(e) => setFVisibility(e.target.value)}
                  className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                >
                  {VISIBILITY_OPTIONS.map((v) => (
                    <option key={v.value} value={v.value}>{v.label}</option>
                  ))}
                </select>
              </div>
              {fVisibility === 'public' ? (
                <div>
                  <label className="block text-xs text-muted-foreground mb-1">Target Role</label>
                  <select
                    value={fTargetRole}
                    onChange={(e) => setFTargetRole(e.target.value)}
                    className="w-full px-3 py-2 bg-muted border border-border rounded-lg text-sm text-foreground"
                  >
                    {TARGET_ROLE_OPTIONS.map((v) => (
                      <option key={v.value} value={v.value}>{v.label}</option>
                    ))}
                  </select>
                </div>
              ) : (
                <div className="flex items-end">
                  <label className="flex items-center gap-2 text-sm text-foreground/80 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={fPinned}
                      onChange={(e) => setFPinned(e.target.checked)}
                      className="rounded border-border"
                    />
                    📌 Pin this article
                  </label>
                </div>
              )}
            </div>

            {fVisibility === 'public' && (
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-2 text-sm text-foreground/80 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={fPinned}
                    onChange={(e) => setFPinned(e.target.checked)}
                    className="rounded border-border"
                  />
                  📌 Pin this article
                </label>
              </div>
            )}

            {fVisibility === 'public' && (
              <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-lg text-sm text-yellow-700 dark:text-yellow-400">
                ⚠️ Public articles require admin approval before they become visible to other companies.
                Only HTTPS links are allowed for security.
              </div>
            )}

            <div className="flex gap-3">
              <button
                type="submit"
                disabled={saving || !fTitle.trim()}
                className="px-4 py-2 bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground text-sm font-medium rounded-lg transition-colors"
              >
                {saving ? 'Saving...' : editing ? 'Update' : 'Create'}
              </button>
              <button
                type="button"
                onClick={resetForm}
                className="px-4 py-2 bg-muted hover:bg-muted/80 text-foreground/80 text-sm rounded-lg transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Articles list */}
      {loading ? (
        <div className="text-center py-12 text-muted-foreground">Loading...</div>
      ) : articles.length === 0 ? (
        <div className="text-center py-12">
          <p className="text-muted-foreground text-lg">No articles yet</p>
          <p className="text-muted-foreground text-sm mt-2">
            {canManage
              ? 'Click "New Article" to add tips, guides, and documentation for your team.'
              : 'Your admin can add helpful tips and guides here.'}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {articles.map((a) => (
            <div
              key={a.id}
              className="bg-card border border-border rounded-xl overflow-hidden hover:border-border transition-colors"
            >
              {/* Article header — clickable */}
              <button
                onClick={() => setExpanded(expanded === a.id ? null : a.id)}
                className="w-full flex items-center gap-3 p-4 text-left"
              >
                <span className="text-lg shrink-0">{mediaIcon(a.media_type)}</span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    {!!a.pinned && <span className="text-xs">📌</span>}
                    <span className="font-medium text-foreground truncate">{a.title}</span>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                    <span>{getCatLabel(a.category)}</span>
                    {a.visibility === 'public' ? (
                      a.approved ? (
                        <span className="px-1.5 py-0.5 bg-green-500/10 border border-green-500/30 rounded text-green-700 dark:text-green-400">
                          🌐 Public{a.target_role && a.target_role !== 'all' ? ` · ${a.target_role}` : ''}
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.5 bg-yellow-500/10 border border-yellow-500/30 rounded text-yellow-700 dark:text-yellow-400">
                          ⏳ Pending Approval
                        </span>
                      )
                    ) : (
                      <span className="px-1.5 py-0.5 bg-muted border border-border rounded text-muted-foreground">
                        🔒 Private
                      </span>
                    )}
                    <span>{new Date(a.created_at).toLocaleDateString()}</span>
                    {a.creator_name && (
                      <span className="text-muted-foreground">by {a.creator_name}</span>
                    )}
                  </div>
                </div>
                <span className="text-muted-foreground text-sm shrink-0">
                  {expanded === a.id ? '▲' : '▼'}
                </span>
              </button>

              {/* Expanded details */}
              {expanded === a.id && (
                <div className="border-t border-border p-4 space-y-3">
                  {a.description && (
                    <p className="text-sm text-foreground/80 whitespace-pre-wrap">{a.description}</p>
                  )}
                  {a.tags && (
                    <div className="flex flex-wrap gap-1.5">
                      {a.tags.split(',').map((tag, i) => (
                        <span
                          key={i}
                          className="px-2 py-0.5 bg-muted border border-border rounded-full text-xs text-muted-foreground"
                        >
                          #{tag.trim()}
                        </span>
                      ))}
                    </div>
                  )}
                  {a.media_url && (
                    <a
                      href={a.media_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-2 px-3 py-1.5 bg-primary/15 border border-primary/30 rounded-lg text-sm text-primary hover:bg-primary/25 transition-colors"
                    >
                      {mediaIcon(a.media_type)}
                      {a.media_type === 'video' ? 'Watch Video' :
                       a.media_type === 'pdf' ? 'Open PDF' :
                       a.media_type === 'image' ? 'View Image' : 'Open Link'}
                    </a>
                  )}
                  {a.created_by === myTelegramId && (
                    <div className="flex gap-2 pt-2">
                      <button
                        onClick={() => openEdit(a)}
                        className="px-3 py-1 bg-muted hover:bg-muted/80 text-foreground/80 text-xs rounded transition-colors"
                      >
                        ✏️ Edit
                      </button>
                      <button
                        onClick={() => handleDelete(a.id)}
                        className="px-3 py-1 bg-destructive/10 hover:bg-destructive/20 border border-destructive/30 text-destructive text-xs rounded transition-colors"
                      >
                        🗑 Delete
                      </button>
                    </div>
                  )}
                  {isAdmin && a.visibility === 'public' && !a.approved && (
                    <div className="flex gap-2 pt-2 border-t border-border mt-2">
                      <span className="text-xs text-yellow-600 dark:text-yellow-400 self-center mr-2">⚠️ Needs approval:</span>
                      <button
                        onClick={() => handleApprove(a.id)}
                        className="px-3 py-1 bg-green-500/10 hover:bg-green-500/20 border border-green-500/30 text-green-700 dark:text-green-400 text-xs rounded transition-colors"
                      >
                        ✅ Approve
                      </button>
                      <button
                        onClick={() => handleReject(a.id)}
                        className="px-3 py-1 bg-destructive/10 hover:bg-destructive/20 border border-destructive/30 text-destructive text-xs rounded transition-colors"
                      >
                        ❌ Reject
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
