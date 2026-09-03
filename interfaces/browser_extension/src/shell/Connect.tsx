import { useEffect, useState } from 'react';
import { beginConnect, clearPending } from '../connect';

/**
 * The panel's first screen.  No fields: the person signs in on 4truck.us
 * — URL bar in view — and confirms there; the token arrives through the
 * service worker and this screen goes away on its own.
 */
export default function Connect({ onDone }: { onDone: () => void }) {
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState('');

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
      <p className="muted" style={{ margin: 0 }}>
        You confirm on 4truck.us — this panel never asks for a password. Once connected it
        shows the vehicles you are allowed to see, live, and nothing else.
      </p>
      {!waiting ? (
        <button className="btn primary" type="button" onClick={() => void start()}>Connect to 4truck</button>
      ) : (
        <>
          <p className="muted" style={{ margin: 0 }}>Waiting for you to confirm in the 4truck tab…</p>
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
