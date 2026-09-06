/**
 * Where a signed-in person gets the browser extension — from the Chrome
 * Web Store, linked from inside the product.
 *
 * The store is the door for everyone: one click, and updates arrive on
 * their own.  The zip is the second door — a build the store does not
 * have yet (a preview, a fix still under review) is loaded unpacked from
 * it, which is why the download stays and stands second.
 *
 * The id is the PACKAGE's, one for every install and every account;
 * tenancy comes from signing in inside the panel, never from the
 * extension.  The store page is addressed by that same id, so the link
 * is derived here, not configured anywhere.
 */
import { useEffect, useState } from 'react';
import { Download, ExternalLink, Puzzle } from 'lucide-react';
import { toast } from '../../lib/toast';

import { apiFetch, apiJSON } from '../../api/client';
import { Button } from '../../components/ui/button';
import { Tip } from '../../components/tooltip';
import { Card } from '@/components/ui/card';

interface Info { built: boolean; version: string; extension_id: string; }

const storeUrl = (id: string) => `https://chromewebstore.google.com/detail/${id}`;

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

  const store = info?.extension_id ? storeUrl(info.extension_id) : null;

  return (
    <Card render={<section />}>
      <div className="flex items-center gap-2 mb-1">
        <Puzzle className="text-muted-foreground size-4.5" />
        <h2 className="text-lg font-semibold">Browser extension</h2>
        {info?.version && <span className="ml-auto text-xs text-muted-foreground">v{info.version}</span>}
      </div>
      <p className="text-xs text-muted-foreground mb-4">
        4truck in a Chrome side panel beside whatever you are working on — Google Maps,
        a load board, email. Today it shows your vehicles live on the map; more of 4truck
        reaches the panel over time. It never asks for a password: you connect it from
        here, confirm once, and it sees what it shows and nothing else.
      </p>

      <div className="flex flex-wrap items-center gap-2">
        {store && (
          <Button size="sm" render={<a href={store} target="_blank" rel="noopener noreferrer" />}>
            Add to Chrome
            <ExternalLink data-icon="inline-end" aria-hidden />
          </Button>
        )}
        <Tip label="The current build as a zip, for loading unpacked from chrome://extensions — a build the store does not have yet.">
          <Button type="button" size="sm" variant={store ? 'ghost' : 'default'}
                  onClick={() => void download()} disabled={busy || info?.built === false}>
            <Download />
            {busy ? 'Preparing…' : 'Download zip'}
          </Button>
        </Tip>
      </div>
      {info?.built === false && (
        <p className="text-xs text-muted-foreground mt-2">Not built on this server yet.</p>
      )}

      <ol className="mt-3 space-y-1 text-xs text-muted-foreground list-decimal pl-4">
        <li><b>Add to Chrome</b> opens the Chrome Web Store; Chrome keeps it updated from there.</li>
        <li>Click the 4truck icon in the toolbar (pin it from the puzzle menu), then <b>Connect to 4truck</b> — a page opens here where you confirm.</li>
      </ol>
      <p className="text-2xs text-muted-foreground mt-2">
        One permanent id for every install — nothing to copy or configure. Every connection
        sends you a sign-in notice with a <b>Disconnect this session</b> button; it also shows
        as “Browser extension” under Active sessions above.
      </p>
    </Card>
  );
}
