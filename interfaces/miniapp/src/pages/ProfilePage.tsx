/**
 * ProfilePage — driver-facing profile / settings.
 *
 * Visual: avatar header, grouped settings rows that open BottomSheets for
 * language picker and quiet-hours editor. Permission summary collapses to
 * a single tap-to-view row. Snackbar confirms saves.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Icon24WarningTriangleOutline,
  Icon24GlobeOutline,
  Icon24NotificationOutline,
  Icon24SpeedometerMiddleOutline,
  Icon24KeyOutline,
  Icon24CheckShieldOutline,
  Icon24CheckCircleOutline,
  Icon24CancelCircleOutline,
  Icon24ViewOutline,
  Icon24HideOutline,
  Icon24UserCircleOutline,
} from '@vkontakte/icons';
import { useTranslation } from 'react-i18next';
import { apiFetch, apiJSON } from '../api/client';
import { BottomSheet } from '../components/BottomSheet';
import { Snackbar } from '../components/Snackbar';
import { ScoreSkeleton } from '../components/Skeleton';
import { MyDriverInfo } from '../components/MyDriverInfo';
import { haptics } from '../hooks/useTelegram';
import { useUnitSystem } from '../hooks/useUnitSystem';
import { setUnitSystem, type UnitSystem } from '../utils/units';

interface UserMe {
  telegram_id: number;
  display_name: string;
  role: string;
  department?: string | null;
  account_id: number;
  truck_num?: string | null;
  trucks: string[];
  allowed_companies: string[];
  language: string | null;
  timezone: string | null;
  quiet_start: number | null;
  quiet_end: number | null;
  email: string | null;
  has_password: boolean;
  permissions: Record<string, boolean>;
}

// Same order + native-script labels as the dashboard's
// ``LANGUAGE_OPTIONS`` (interfaces/dashboard/src/utils/languages.ts).
// Native scripts read correctly regardless of the user's current UI
// language — a Punjabi speaker recognises ਪੰਜਾਬੀ instantly, whereas
// the previous "Punjabi" / "Amharic" labels assumed English.
const LANG_OPTIONS = [
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Español' },
  { value: 'fr', label: 'Français' },
  { value: 'pa', label: 'ਪੰਜਾਬੀ' },
  { value: 'ru', label: 'Русский' },
  { value: 'so', label: 'Soomaali' },
  { value: 'uk', label: 'Українська' },
  { value: 'uz', label: 'Oʻzbekcha' },
  { value: 'am', label: 'አማርኛ' },
];

const ROLE_LABELS: Record<string, string> = {
  owner: 'Owner',
  admin: 'Admin',
  fleet: 'Fleet',
  safety: 'Safety',
  dispatcher: 'Dispatcher',
  hr: 'HR',
  accounting: 'Accounting',
  driver: 'Driver',
};

const AVATAR_COLORS = ['#34c759', '#0a84ff', '#bf5af2', '#ff9f0a', '#ff453a', '#30b0c7'];

function avatarColor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) & 0xffff;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length];
}

function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function langLabel(code: string | null): string {
  return LANG_OPTIONS.find(o => o.value === code)?.label ?? 'English';
}

function permGroups(perms: Record<string, boolean>): { label: string; on: boolean }[] {
  return [
    { label: 'See own vehicle on map',     on: !!(perms.can_view_location) },
    { label: 'See alerts for own vehicle', on: !!perms.can_view_vehicles },
    { label: 'See own scorecard',          on: !!perms.can_view_scorecards },
    { label: 'See own routes',             on: !!(perms.can_view_routes) },
    { label: 'See own maintenance',        on: !!perms.can_view_maintenance },
    { label: 'See own safety events',      on: !!(perms.can_view_events) },
  ];
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map(w => w[0]?.toUpperCase() ?? '')
    .join('') || '?';
}

// Backend stores quiet hours as integer hour (0–23). HTML <input type="time">
// expects "HH:mm". Convert at the boundary so the picker doesn't warn and the
// label reads naturally.
function hourToHHMM(h: number | null): string {
  if (h === null || h === undefined) return '';
  return `${String(h).padStart(2, '0')}:00`;
}

function hhmmToHour(s: string): number | null {
  if (!s) return null;
  const h = parseInt(s.slice(0, 2), 10);
  return Number.isFinite(h) ? h : null;
}

function quietLabel(start: number | null, end: number | null): string {
  if (start === null || end === null) return 'Off';
  return `${hourToHHMM(start)} – ${hourToHHMM(end)}`;
}

export function ProfilePage() {
  const { i18n, t } = useTranslation();
  const [me, setMe] = useState<UserMe | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const [snackOpen, setSnackOpen] = useState(false);
  const [snackText, setSnackText] = useState('');
  const [snackKind, setSnackKind] = useState<'success' | 'error' | 'info'>('success');

  const [langSheetOpen, setLangSheetOpen] = useState(false);
  const [quietSheetOpen, setQuietSheetOpen] = useState(false);
  const [permSheetOpen, setPermSheetOpen] = useState(false);
  const [credSheetOpen, setCredSheetOpen] = useState(false);
  const [unitSheetOpen, setUnitSheetOpen] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const units = useUnitSystem();

  // Edit buffers
  const [quietStart, setQuietStart] = useState('');
  const [quietEnd, setQuietEnd] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  // In-flight flags so Save buttons disable while the PUT is pending —
  // protects against double-tap submitting the same payload twice.
  const [savingQuiet, setSavingQuiet] = useState(false);
  const [savingCreds, setSavingCreds] = useState(false);
  const [savingLang, setSavingLang] = useState(false);

  const photoUrl = useMemo<string | null>(() => {
    try {
      return (window as any).Telegram?.WebApp?.initDataUnsafe?.user?.photo_url ?? null;
    } catch { return null; }
  }, []);

  const load = useCallback(async () => {
    setError(false);
    try {
      const data = await apiJSON<UserMe>('/api/user/me');
      setMe(data);
      setQuietStart(hourToHHMM(data.quiet_start));
      setQuietEnd(hourToHHMM(data.quiet_end));
      setEmail(data.email ?? '');
    } catch (e) {
      console.error('Failed to load profile:', e);
      setError(true);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    load().finally(() => setLoading(false));
  }, [load]);

  function toast(text: string, kind: 'success' | 'error' | 'info' = 'success') {
    setSnackText(text);
    setSnackKind(kind);
    setSnackOpen(true);
  }

  async function saveLanguage(next: string) {
    haptics.selection();
    setLangSheetOpen(false);
    if (next === me?.language) return;
    if (savingLang) return;
    setSavingLang(true);
    // Switch i18next first so the UI flips immediately, before the
    // backend round-trip. localStorage persistence is baked into the
    // detector config so a reload picks up the same choice.
    void i18n.changeLanguage(next);
    try {
      await apiFetch('/api/user/preferences', {
        method: 'PUT',
        body: { language: next },
      });
      setMe(m => (m ? { ...m, language: next } : m));
      haptics.success();
      toast(t('toasts.language_saved'));
    } catch (e) {
      console.error('Save language failed:', e);
      haptics.error();
      toast(t('toasts.language_save_failed'), 'error');
    } finally {
      setSavingLang(false);
    }
  }

  async function saveQuietHours() {
    if (savingQuiet) return;
    const startHour = hhmmToHour(quietStart);
    const endHour = hhmmToHour(quietEnd);
    setSavingQuiet(true);
    try {
      await apiFetch('/api/user/preferences', {
        method: 'PUT',
        body: { quiet_start: startHour, quiet_end: endHour },
      });
      setMe(m => (m ? { ...m, quiet_start: startHour, quiet_end: endHour } : m));
      setQuietSheetOpen(false);
      haptics.success();
      toast(t('toasts.quiet_saved'));
    } catch (e) {
      console.error('Save quiet hours failed:', e);
      haptics.error();
      toast(t('toasts.quiet_save_failed'), 'error');
    } finally {
      setSavingQuiet(false);
    }
  }

  async function saveCredentials() {
    if (savingCreds) return;
    if (!email || password.length < 8) return;
    setSavingCreds(true);
    try {
      await apiFetch('/api/user/credentials', {
        method: 'PUT',
        body: { email, password },
      });
      setPassword('');
      setShowPassword(false);
      setCredSheetOpen(false);
      haptics.success();
      toast(t('toasts.credentials_saved'));
      load();
    } catch (e) {
      console.error('Save credentials failed:', e);
      haptics.error();
      toast(t('toasts.credentials_save_failed'), 'error');
    } finally {
      setSavingCreds(false);
    }
  }

  const perms = useMemo(() => (me ? permGroups(me.permissions) : []), [me]);
  const enabledPerms = useMemo(() => perms.filter(p => p.on).length, [perms]);

  if (loading) return <ScoreSkeleton />;

  if (error || !me) {
    return (
      <div className="centered">
        <div className="profile-error">
          <Icon24WarningTriangleOutline width={48} height={48} style={{ opacity: 0.5 }} />
          <p>Could not load your profile.</p>
          <button
            className="profile-save-btn"
            onClick={() => { setLoading(true); load().finally(() => setLoading(false)); }}
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const isDriver = me.role === 'driver';
  const vehicleText = me.trucks.length
    ? me.trucks.join(', ')
    : isDriver
      ? (me.truck_num || 'Not assigned — contact your admin')
      : null; // non-drivers have no truck assignment — hide the row

  const companyText = me.allowed_companies.join(', ');

  return (
    <div className="profile-page">

      {/* ── Identity header ────────────────────────────────────── */}
      <div className="profile-header">
        <div
          className="profile-avatar"
          style={{ background: photoUrl ? 'transparent' : avatarColor(me.display_name) }}
          aria-hidden
        >
          {photoUrl
            ? <img
                src={photoUrl}
                alt=""
                className="profile-avatar__photo"
                onError={e => { (e.target as HTMLImageElement).style.display = 'none'; }}
              />
            : initials(me.display_name)
          }
        </div>
        <div>
          <div className="profile-name">{me.display_name}</div>
          <div className="profile-role">
            <Icon24UserCircleOutline width={14} height={14} />
            {roleLabel(me.role)}
          </div>
        </div>
      </div>

      {/* ── Assignment ────────────────────────────────────────── */}
      <div className="profile-section">
        <div className="profile-section__header">{t('profile.assignment')}</div>
        {vehicleText && (
          <div className="profile-row profile-row--static">
            <span className="profile-row__label">{t('profile.vehicle_label')}</span>
            <span className="profile-row__value">{vehicleText}</span>
          </div>
        )}
        <div className="profile-row profile-row--static">
          <span className="profile-row__label">{t('profile.company')}</span>
          <span className="profile-row__value">{companyText}</span>
        </div>
        {me.department && (
          <div className="profile-row profile-row--static">
            <span className="profile-row__label">{t('profile.department')}</span>
            <span className="profile-row__value">{me.department}</span>
          </div>
        )}
      </div>

      {/* ── Driver self-view (docs + assigned vehicle) ───────
          MyDriverInfo hides itself for non-drivers, so it's
          safe to render unconditionally here. */}
      <MyDriverInfo onToast={toast} />

      {/* ── Preferences ──────────────────────────────────────── */}
      <div className="profile-section">
        <div className="profile-section__header">{t('profile.preferences')}</div>
        <button className="profile-row" onClick={() => { haptics.selection(); setLangSheetOpen(true); }}>
          <span className="profile-row__icon"><Icon24GlobeOutline width={20} height={20} /></span>
          <span className="profile-row__label">{t('profile.language_section')}</span>
          <span className="profile-row__value">{langLabel(me.language)} ›</span>
        </button>
        <button className="profile-row" onClick={() => { haptics.selection(); setQuietSheetOpen(true); }}>
          <span className="profile-row__icon"><Icon24NotificationOutline width={20} height={20} /></span>
          <span className="profile-row__label">{t('profile.quiet_hours')}</span>
          <span className="profile-row__value">{quietLabel(me.quiet_start, me.quiet_end)} ›</span>
        </button>
        <button className="profile-row" onClick={() => { haptics.selection(); setUnitSheetOpen(true); }}>
          <span className="profile-row__icon"><Icon24SpeedometerMiddleOutline width={20} height={20} /></span>
          <span className="profile-row__label">{t('profile.units')}</span>
          <span className="profile-row__value">{units === 'metric' ? t('profile.units_metric') : t('profile.units_imperial')} ›</span>
        </button>
      </div>

      {/* ── Dashboard login ──────────────────────────────────── */}
      <div className="profile-section">
        <div className="profile-section__header">{t('profile.dashboard_login')}</div>
        <button
          className="profile-row"
          onClick={() => { haptics.selection(); setCredSheetOpen(true); }}
        >
          <span className="profile-row__icon"><Icon24KeyOutline width={20} height={20} /></span>
          <span className="profile-row__label">
            {me.has_password && me.email ? t('profile.email') : t('profile.set_email_password')}
          </span>
          <span className="profile-row__value">
            {me.has_password && me.email ? `${me.email} ›` : '›'}
          </span>
        </button>
        {!me.has_password && (
          <p className="profile-section__footer">
            {t('profile.credentials_hint')}
          </p>
        )}
      </div>

      {/* ── Access ───────────────────────────────────────────── */}
      <div className="profile-section">
        <div className="profile-section__header">{t('profile.access')}</div>
        <button className="profile-row" onClick={() => { haptics.selection(); setPermSheetOpen(true); }}>
          <span className="profile-row__icon"><Icon24CheckShieldOutline width={20} height={20} /></span>
          <span className="profile-row__label">{t('profile.features_enabled')}</span>
          <span className="profile-row__value">{enabledPerms} of {perms.length} ›</span>
        </button>
      </div>

      {/* ── Footer ───────────────────────────────────────────── */}
      <p className="profile-footer-note">Telegram ID {me.telegram_id}</p>
      <div style={{ textAlign: 'center', paddingBottom: 32 }}>
        <button
          className="profile-close-btn"
          onClick={() => (window as any).Telegram?.WebApp?.close?.()}
        >
          Close app
        </button>
      </div>

      {/* ── Language sheet ─────────────────────────────────────── */}
      <BottomSheet
        open={langSheetOpen}
        onClose={() => setLangSheetOpen(false)}
        title={
          <span className="sheet__title-inner">
            <Icon24GlobeOutline width={20} height={20} />
            {t('profile.choose_language')}
          </span>
        }
      >
        {LANG_OPTIONS.map(o => (
          <button key={o.value} className="profile-row" onClick={() => saveLanguage(o.value)}>
            <span className="profile-row__label">{o.label}</span>
            {me.language === o.value && (
              <Icon24CheckCircleOutline
                width={20} height={20}
                style={{ color: 'var(--tg-theme-button-color, var(--st-blue))', flexShrink: 0 }}
              />
            )}
          </button>
        ))}
      </BottomSheet>

      {/* ── Quiet hours sheet ──────────────────────────────────── */}
      <BottomSheet
        open={quietSheetOpen}
        onClose={() => setQuietSheetOpen(false)}
        title={
          <span className="sheet__title-inner">
            <Icon24NotificationOutline width={20} height={20} />
            {t('profile.quiet_hours')}
          </span>
        }
      >
        <p className="sheet-body__desc">
          {t('profile.quiet_desc')}
          {me.timezone && <> {t('profile.quiet_timezone')} <strong>{me.timezone}</strong>.</>}
        </p>
        <div style={{ display: 'flex', gap: 12 }}>
          <label className="profile-input-label" style={{ flex: 1 }}>
            {t('profile.quiet_start')}
            <input
              type="time"
              value={quietStart}
              onChange={e => setQuietStart(e.target.value)}
              className="profile-time-input"
            />
          </label>
          <label className="profile-input-label" style={{ flex: 1 }}>
            {t('profile.quiet_end')}
            <input
              type="time"
              value={quietEnd}
              onChange={e => setQuietEnd(e.target.value)}
              className="profile-time-input"
            />
          </label>
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          <button
            className="profile-secondary-btn"
            disabled={savingQuiet}
            onClick={() => { setQuietStart(''); setQuietEnd(''); saveQuietHours(); }}
          >
            {t('profile.quiet_turn_off')}
          </button>
          <button
            className="profile-save-btn"
            style={{ flex: 1 }}
            disabled={savingQuiet}
            onClick={saveQuietHours}
          >
            {savingQuiet ? t('profile.saving') : t('profile.saved')}
          </button>
        </div>
      </BottomSheet>

      {/* ── Credentials sheet ──────────────────────────────────── */}
      <BottomSheet
        open={credSheetOpen}
        onClose={() => { setCredSheetOpen(false); setShowPassword(false); }}
        title={
          <span className="sheet__title-inner">
            <Icon24KeyOutline width={20} height={20} />
            {me.has_password ? t('profile.update_credentials') : t('profile.dashboard_login')}
          </span>
        }
      >
        <p className="sheet-body__desc">
          {me.has_password
            ? t('profile.credentials_desc_update')
            : t('profile.credentials_desc_set')}
        </p>
        <label className="profile-input-label">
          {t('profile.email')}
          <input
            type="email"
            placeholder={t('profile.email_placeholder')}
            value={email}
            onChange={e => setEmail(e.target.value)}
            className="profile-time-input"
          />
        </label>
        <label className="profile-input-label" style={{ marginTop: 12 }}>
          {t('profile.password_label')}
          <div className="profile-password-wrap">
            <input
              type={showPassword ? 'text' : 'password'}
              placeholder={t('profile.password_placeholder')}
              value={password}
              onChange={e => setPassword(e.target.value)}
              className="profile-time-input profile-time-input--password"
            />
            <button
              className="profile-password-toggle"
              onClick={() => setShowPassword(v => !v)}
              type="button"
              aria-label={showPassword ? t('profile.hide_password') : t('profile.show_password')}
            >
              {showPassword
                ? <Icon24HideOutline width={20} height={20} />
                : <Icon24ViewOutline width={20} height={20} />
              }
            </button>
          </div>
        </label>
        {password.length > 0 && password.length < 8 && (
          <p className="profile-input-hint">{t('profile.password_min_hint')}</p>
        )}
        <button
          className="profile-save-btn"
          style={{ width: '100%', marginTop: 16 }}
          disabled={!email || password.length < 8 || savingCreds}
          onClick={saveCredentials}
        >
          {savingCreds
            ? t('profile.saving')
            : me.has_password ? t('profile.update_credentials') : t('profile.save_credentials')}
        </button>
      </BottomSheet>

      {/* ── Units sheet ────────────────────────────────────────── */}
      <BottomSheet
        open={unitSheetOpen}
        onClose={() => setUnitSheetOpen(false)}
        title={
          <span className="sheet__title-inner">
            <Icon24SpeedometerMiddleOutline width={20} height={20} />
            {t('profile.choose_units')}
          </span>
        }
      >
        {(['imperial', 'metric'] as const).map(u => (
          <button
            key={u}
            className="profile-row"
            onClick={() => {
              setUnitSystem(u as UnitSystem);
              haptics.success();
              setUnitSheetOpen(false);
              toast(`Units set to ${u === 'metric' ? 'metric' : 'imperial'}`);
            }}
          >
            <span className="profile-row__label">
              {u === 'metric' ? t('profile.units_metric_full') : t('profile.units_imperial_full')}
            </span>
            {units === u && (
              <Icon24CheckCircleOutline
                width={20} height={20}
                style={{ color: 'var(--tg-theme-button-color, var(--st-blue))', flexShrink: 0 }}
              />
            )}
          </button>
        ))}
      </BottomSheet>

      {/* ── Permission sheet ───────────────────────────────────── */}
      <BottomSheet
        open={permSheetOpen}
        onClose={() => setPermSheetOpen(false)}
        title={
          <span className="sheet__title-inner">
            <Icon24CheckShieldOutline width={20} height={20} />
            {t('profile.your_access')}
          </span>
        }
      >
        {perms.map(p => (
          <div key={p.label} className="profile-perm-row">
            <span className="profile-perm-row__icon">
              {p.on
                ? <Icon24CheckCircleOutline width={20} height={20} style={{ color: 'var(--st-green)' }} />
                : <Icon24CancelCircleOutline width={20} height={20} style={{ color: 'var(--tgui--hint_color)' }} />
              }
            </span>
            <span className="profile-perm-row__label">{p.label}</span>
          </div>
        ))}
        {enabledPerms < perms.length && (
          <p className="sheet-body__desc" style={{ marginTop: 12 }}>
            {t('profile.contact_admin_for_more')}
          </p>
        )}
      </BottomSheet>

      <Snackbar open={snackOpen} text={snackText} kind={snackKind} onClose={() => setSnackOpen(false)} />
    </div>
  );
}
