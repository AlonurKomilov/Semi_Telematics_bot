/**
 * Where a signed-in person gets the browser extension — inside the
 * product, not from a file somebody was sent.
 *
 * Until it is on the Chrome Web Store this is a sideload: download the
 * zip the server builds, load it unpacked.  The id is the PACKAGE's,
 * one for every install and every account; tenancy comes from signing
 * in inside the panel, never from the extension.
 */
import { useEffect, useState } from 'react';
import { Download, Puzzle } from 'lucide-react';
import { toast } from 'sonner';

import { apiFetch, apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Card } from '@/components/ui/card';

interface Info { built: boolean; version: string; extension_id: string; }

export default function BrowserExtensionCard() {
  const [info, setInfo] = useState<Info | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiJSON<Info>('/extension/info').then(setInfo).catch(() => setInfo(null));
  }, []);

  const download = async () => {
    setBusy(true);
    try {
      // Through apiFetch for the auth header, then a blob URL — a plain
      // href would arrive without the bearer token.
      const res = await apiFetch('/extension/download', {});
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : 'Download failed');
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement('a');
      a.href = url; a.download = '4truck-extension.zip'; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Download failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card render={<section />}>
      <div className="flex items-center gap-2 mb-1">
        <Puzzle className="text-muted-foreground size-4.5" />
        <h2 className="text-lg font-semibold">Browser extension</h2>
        {info?.version && <span className="ml-auto text-xs text-muted-foreground">v{info.version}</span>}
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        Your trucks, live, in a Chrome side panel beside whatever you are working on —
        Google Maps, a load board, email. Sign in inside the panel with your 4truck email
        and password; it sees the live map and nothing else.
      </p>

      <Button type="button" size="sm" onClick={() => void download()} disabled={busy || info?.built === false}>
        <Download />
        {busy ? 'Preparing…' : 'Download for Chrome'}
      </Button>
      {info?.built === false && (
        <p className="text-xs text-muted-foreground mt-2">Not built on this server yet.</p>
      )}

      <ol className="mt-3 space-y-1 text-xs text-muted-foreground list-decimal pl-4">
        <li>Unzip the download into a folder you will keep.</li>
        <li>Open <span className="font-mono">chrome://extensions</span>, turn on <b>Developer mode</b>, click <b>Load unpacked</b>, pick that folder.</li>
        <li>Click the 4truck icon in the toolbar and sign in.</li>
      </ol>
      <p className="text-2xs text-muted-foreground mt-2">
        One permanent id for every install — nothing to copy or configure. Revoke it any
        time from Active sessions above, where it shows as “Browser extension”.
      </p>
    </Card>
  );
}
