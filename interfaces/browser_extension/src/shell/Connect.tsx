import { useEffect, useState } from 'react';
import { REGISTER_URL, beginConnect, clearPending, getPending } from '../connect';

/**
 * The panel's first screen.  No fields: the person signs in on 4truck.us
 * — URL bar in view — and confirms there; the token arrives through the
 * service worker and this screen goes away on its own.  A person with no
 * account is sent to the same site's Register tab: still no field here.
 */
export default function Connect({ onDone, disconnected = false }: { onDone: () => void; disconnected?: boolean }) {
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState('');

  // `waiting` is a fact about the WORLD, not about this component.  It was
  // local state only, so closing and reopening the panel mid-flow reset the
  // screen to step one while a live pending connection was still sitting in
  // storage — the person then saw "Connect" again and had no idea the tab
  // they had already opened was still the one that would work.
  useEffect(() => {
    let gone = false;
    void getPending().then((p) => {
      if (!gone && p && p.expires > Date.now()) setWaiting(true);
    });
    return () => { gone = true; };
  }, []);

  useEffect(() => {
    const onChange = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'local' && typeof changes.jwt?.newValue === 'string') onDone();
    };
    chrome.storage.onChanged.addListener(onChange);
    return () => chrome.storage.onChanged.removeListener(onChange);
  }, [onDone]);

  const start = async () => {
    setError('');
    try {
      await beginConnect();
      setWaiting(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not open 4truck');
    }
  };
  const cancel = async () => {
    await clearPending();
    setWaiting(false);
  };

  return (
    <div style={{ padding: 16, display: 'grid', gap: 10 }}>
      <h1 style={{ fontSize: 16, margin: 0 }}>Connect to 4truck</h1>
      {/* Three steps, and the first is already done by being here — a
          flow that shows where you are is finished more often than one
          that just says "waiting". */}
      <p className="muted small" style={{ margin: 0 }}>{waiting ? 'Step 2 of 3' : 'Step 1 of 3'}</p>
      {disconnected && (
        <p style={{ margin: 0, color: 'var(--warn)' }}>
          This connection was ended — from your 4truck profile, or it expired. Connect again to continue.
        </p>
      )}
      <p className="muted" style={{ margin: 0 }}>
        You confirm on 4truck.us — this panel never asks for a password. Once
        connected it shows the vehicles you are allowed to see, live, and what is
        aboard them. If your account lets you manage inventory, it can also record
        a check, flag an item, add one or correct one. It can do nothing else — it
        cannot retire an item, move one between vehicles, or reach any other part
        of your account.
      </p>
      {/* Outside the branch: restoring `waiting` from a live pending
          connection meant somebody with NO account met a screen that
          offered them no way to make one, for up to ten minutes. */}
      <p className="muted small" style={{ margin: 0 }}>
        No 4truck account yet?{' '}
        <a className="link" href={REGISTER_URL} target="_blank" rel="noopener noreferrer">Create one on 4truck.us</a>
        {' '}— then come back here and press Connect.
      </p>
      {!waiting ? (
        <>
          <button className="btn primary" type="button" onClick={() => void start()}>Connect to 4truck</button>
        </>
      ) : (
        <>
          {/* The tab may open on the sign-in page first, and a person
              looking for a Connect button that is one screen away
              concludes the extension is broken. */}
          <p className="muted" style={{ margin: 0 }}>
            Waiting for you in the 4truck tab — sign in first if it asks, then press
            <strong> Connect</strong> there.
          </p>
          <div className="row">
            <button className="btn" type="button" onClick={() => void start()}>Open it again</button>
            <button className="btn" type="button" onClick={() => void cancel()}>Cancel</button>
          </div>
        </>
      )}
      {error && <p style={{ color: 'var(--danger)', margin: 0 }}>{error}</p>}
    </div>
  );
}
