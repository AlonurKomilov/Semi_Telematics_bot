/**
 * One thin strip: a newer panel exists, and what that means for YOU.
 *
 * Deliberately not a modal, not a toast, and not a thing that steals
 * the feature's height — it sits between the header and the feature,
 * takes one line, and can be closed.  The reader is in the middle of
 * looking at trucks; an update is news, not an interruption.
 *
 * WHAT IT COMPARES, precisely: the version this panel was built at
 * against the version the SERVER has built.  That is the right question
 * for the sideload reader — `/extension/download` streams exactly that
 * build, so "newer on the server" means "newer in your hands the moment
 * you fetch it".
 *
 * It is a weaker claim for the store reader, and the wording says so
 * rather than pretending: the server builds the moment code lands, the
 * Web Store publishes after a review, so a store install can be behind
 * the server for days through nobody's fault.  Telling that reader to
 * "update now" would be an instruction they cannot carry out.  They are
 * told the version exists and that Chrome handles it, and offered a
 * check they can press if they are impatient.
 */
import { useEffect, useRef, useState } from 'react';

import { apiJSON } from '../api/client';
import { readAppBase } from '../appHost';
import { channelOf, isCheckDue, noticeFor, type Notice } from '../update';

/** Remembered per version, never as a flag — see `noticeFor`. */
const DISMISSED_KEY = 'updateDismissed';
/** The last answer and when it arrived — see `CHECK_EVERY_MS`. */
const LAST_SEEN_KEY = 'updateLastSeen';

async function readDismissed(): Promise<string> {
  try {
    const got = await chrome.storage.local.get(DISMISSED_KEY);
    const v = got[DISMISSED_KEY];
    return typeof v === 'string' ? v : '';
  } catch { return ''; }
}

/**
 * The newest version, from the server or from the last hour's answer.
 *
 * Returns '' when it has neither, which reads as "say nothing" — a
 * panel that cannot reach the server is not a panel that should be
 * complaining about its own version.
 */
async function latestVersion(): Promise<string> {
  let cached = { version: '', at: 0 };
  try {
    const got = await chrome.storage.local.get(LAST_SEEN_KEY);
    const v = got[LAST_SEEN_KEY];
    if (v && typeof v === 'object') {
      cached = { version: String((v as { version?: unknown }).version ?? ''),
                 at: Number((v as { at?: unknown }).at ?? 0) };
    }
  } catch { /* an unreadable cache is an empty one */ }

  if (!isCheckDue(cached.at, Date.now())) return cached.version;

  const info = await apiJSON<{ version?: string }>('/extension/info').catch(() => null);
  if (!info) return cached.version;          // keep yesterday's answer over none
  const version = String(info.version || '');
  try {
    await chrome.storage.local.set({ [LAST_SEEN_KEY]: { version, at: Date.now() } });
  } catch { /* the notice still works, it just asks again next time */ }
  return version;
}

export default function UpdateNotice() {
  const [notice, setNotice] = useState<Notice | null>(null);
  const [base, setBase] = useState('');
  const [checked, setChecked] = useState<string>('');
  /** The panel is closed mid-gesture all day, so every await in here can
   *  land after unmount — the button handlers below need the same guard
   *  the mount effect has, not a different standard. */
  const live = useRef(true);
  useEffect(() => () => { live.current = false; }, []);

  useEffect(() => {
    let stopped = false;
    void (async () => {
      const manifest = chrome.runtime.getManifest();
      const [latest, dismissed, appBase] = await Promise.all([
        latestVersion(), readDismissed(), readAppBase(),
      ]);
      if (stopped || !latest) return;
      setBase(appBase);
      setNotice(noticeFor({
        // INSTALLED is read fresh every time, never cached: that is what
        // lets the hour-long cache above be safe.  A reader who updates
        // loses the strip on the next open, not an hour later.
        installed: String(manifest.version || ''),
        latest,
        channel: channelOf(manifest as { key?: string }),
        dismissed,
      }));
    })();
    return () => { stopped = true; };
  }, []);

  if (!notice) return null;

  const dismiss = () => {
    if (!live.current) return;
    setNotice(null);
    void chrome.storage.local.set({ [DISMISSED_KEY]: notice.version }).catch(() => {});
  };

  /** Ask Chrome to go and look now, instead of waiting for its own
   *  schedule.  Only store installs can answer this — an unpacked one
   *  throws, which is why the result is reported rather than assumed. */
  const checkNow = async () => {
    try {
      const res = await chrome.runtime.requestUpdateCheck();
      const status = (res as { status?: string })?.status ?? String(res ?? '');
      if (!live.current) return;
      setChecked(status === 'update_available'
        ? 'Chrome is installing it — reopen the panel in a moment.'
        : 'Not published yet. Chrome will pick it up on its own.');
    } catch {
      if (!live.current) return;
      setChecked('This copy updates by hand — use the download below.');
    }
  };

  return (
    <div className="row"
         style={{ gap: 8, padding: '5px 12px', fontSize: 12,
                  borderBottom: '1px solid var(--border)',
                  // `--card` is the panel's own raised-surface token.  An
                  // earlier draft reached for `--accent-soft`, which this
                  // stylesheet has never defined: the fallback would have
                  // worked and the name would have lied.
                  background: 'var(--card)' }}>
      {/* THE MEANING LEADS, THE NUMBER FOLLOWS.  The first draft put
          `0.5.20.0` in bold at the front, which is the least meaningful
          token on the strip to the person reading it: a driver needs to
          know a newer panel exists, and the number is only the reference
          they would quote if they asked us about it. */}
      <span style={{ flex: 1, minWidth: 0 }}>
        {notice.channel === 'sideload' ? (
          <><strong>A newer panel is ready</strong> ({notice.version}). This copy
            was loaded by hand, so it will not update itself — get it from{' '}
            <a href={`${base}/profile`} target="_blank" rel="noreferrer">
              your Profile page
            </a>{' '}and reload it in chrome://extensions.</>
        ) : (
          <><strong>A newer panel is ready</strong> ({notice.version}). Chrome
            installs it on its own once the Web Store publishes it —
            nothing to do.</>
        )}
        {checked && <><br /><span className="muted">{checked}</span></>}
      </span>
      {notice.channel === 'store' && !checked && (
        <button type="button" className="btn compact" onClick={() => void checkNow()}>
          Check now
        </button>
      )}
      <button type="button" className="btn compact" onClick={dismiss}
              aria-label="Dismiss until the next version" title="Dismiss">
        ×
      </button>
    </div>
  );
}
