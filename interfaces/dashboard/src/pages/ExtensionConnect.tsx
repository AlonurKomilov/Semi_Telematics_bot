/**
 * The browser extension's consent step.
 *
 * The panel never holds a password.  It opens THIS page with a one-time
 * ``state``; the person — already signed in here, or signed in a moment
 * ago and landed back on this URL — reads what the extension will be
 * able to see and presses Connect.  Only then does the server mint the
 * live-map-scoped token, and the page hands it to the extension through
 * ``chrome.runtime.sendMessage`` — it never travels through a URL, a
 * clipboard or a server-side code.  The extension accepts it only when
 * the ``state`` matches the one it generated.
 *
 * Outside the dashboard shell on purpose: a consent page has one
 * question and two buttons, not a sidebar.
 */
import { useState } from 'react';
import { Puzzle, ShieldCheck } from 'lucide-react';

import { apiJSON } from '../api/client';
import { Button } from '../components/ui/button';
import { Card } from '@/components/ui/card';

type Phase = 'ask' | 'working' | 'done' | 'no-extension' | 'delivery-failed' | 'error' | 'no-state';

interface ExtRuntime {
  sendMessage?: (id: string, msg: unknown, cb: (reply: unknown) => void) => void;
  lastError?: { message?: string };
}

function runtime(): ExtRuntime | undefined {
  return (window as unknown as { chrome?: { runtime?: ExtRuntime } }).chrome?.runtime;
}

/** Ask the extension something; resolves to its reply or null when it is
 *  not installed / not reachable from this origin. */
function ask(id: string, msg: unknown): Promise<{ ok?: boolean } | null> {
  return new Promise((resolve) => {
    const rt = runtime();
    if (!rt?.sendMessage) { resolve(null); return; }
    try {
      rt.sendMessage(id, msg, (reply) => {
        if (rt.lastError) { resolve(null); return; }
        resolve((reply ?? null) as { ok?: boolean } | null);
      });
    } catch {
      resolve(null);
    }
  });
}

export default function ExtensionConnect() {
  const state = new URLSearchParams(window.location.search).get('state') ?? '';
  const [phase, setPhase] = useState<Phase>(/^[0-9a-f]{64}$/.test(state) ? 'ask' : 'no-state');
  const [error, setError] = useState('');

  const connect = async () => {
    setPhase('working');
    try {
      const info = await apiJSON<{ extension_id: string }>('/extension/info');
      if (!info.extension_id) throw new Error('This server does not know the extension id yet.');
      // Reach the extension BEFORE minting: a token nobody receives is a
      // session with no holder.
      const ping = await ask(info.extension_id, { type: '4truck:ping', state });
      if (!ping?.ok) { setPhase('no-extension'); return; }
      const out = await apiJSON<{ access_token: string }>('/extension/connect', {
        method: 'POST', headers: { 'X-Requested-With': '4truck-dashboard' },
      });
      const delivered = await ask(info.extension_id, { type: '4truck:connect', token: out.access_token, state });
      setPhase(delivered?.ok ? 'done' : 'delivery-failed');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not connect');
      setPhase('error');
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-start justify-center p-6 pt-16">
      <Card render={<section />} className="w-full max-w-md">
        <div className="flex items-center gap-2 mb-3">
          <Puzzle className="text-muted-foreground size-4.5" />
          <h1 className="text-lg font-semibold">Connect 4truck for Chrome</h1>
        </div>

        {phase === 'no-state' && (
          <p className="text-sm text-muted-foreground">
            Open this page from the extension: click the 4truck icon in Chrome's toolbar, then <b>Connect to 4truck</b>.
          </p>
        )}

        {(phase === 'ask' || phase === 'working') && (
          <>
            <p className="text-sm mb-3">
              The extension in this browser wants to connect to your 4truck account.
            </p>
            <ul className="text-sm text-muted-foreground space-y-1.5 mb-4">
              <li className="flex gap-2"><ShieldCheck className="size-4 shrink-0 mt-0.5" /> It can see your vehicles' live positions on the map, and nothing else.</li>
              <li className="flex gap-2"><ShieldCheck className="size-4 shrink-0 mt-0.5" /> It cannot change anything in your account.</li>
              <li className="flex gap-2"><ShieldCheck className="size-4 shrink-0 mt-0.5" /> It appears as “Browser extension” under Active sessions on your profile, where you can disconnect it any time.</li>
            </ul>
            <p className="text-xs text-muted-foreground mb-4">
              Didn't just click Connect in the extension? Press Cancel — nothing is connected until you confirm here.
            </p>
            <div className="flex gap-2">
              <Button type="button" onClick={() => void connect()} disabled={phase === 'working'}>
                {phase === 'working' ? 'Connecting…' : 'Connect'}
              </Button>
              <Button type="button" variant="outline" onClick={() => window.close()} disabled={phase === 'working'}>
                Cancel
              </Button>
            </div>
          </>
        )}

        {phase === 'done' && (
          <>
            <p className="text-sm mb-1">Connected. The extension is signed in — you can close this tab.</p>
            <p className="text-xs text-muted-foreground">
              A “New sign-in — Browser extension” notice is in your inbox with a <b>Disconnect this session</b> button, in case this was not you.
            </p>
          </>
        )}

        {phase === 'no-extension' && (
          <p className="text-sm">
            The extension did not answer. Make sure 4truck for Chrome is installed in <i>this</i> browser profile and that you opened this page from its <b>Connect to 4truck</b> button, then try again.
          </p>
        )}

        {phase === 'delivery-failed' && (
          <p className="text-sm">
            The connection was approved but the extension did not receive it. A “Browser extension” session now exists on your profile — open <a className="underline" href="/profile">Active sessions</a> and sign it out, then try again from the extension.
          </p>
        )}

        {phase === 'error' && (
          <p className="text-sm text-destructive">{error}</p>
        )}
      </Card>
    </div>
  );
}
