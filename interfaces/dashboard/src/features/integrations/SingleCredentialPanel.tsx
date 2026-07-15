/**
 * Connected-state credentials view for single-token providers
 * (Datatruck: api_token_with_subdomain).  The per-company-key
 * providers (Samsara) use ConnectedCompanies instead — both render
 * inside the shared <CredentialsSection> shell so they read alike.
 *
 * Raw tokens are never sent to the client, so this shows only a
 * "configured" status (from has_credentials) + an Update action that
 * reopens the connect form (a re-connect upserts the credentials).
 */

import CredentialsSection from './CredentialsSection';
import { Button } from '../../components/ui/button';
import { toneClasses } from '../../lib/status';
import { Pencil } from 'lucide-react';

export default function SingleCredentialPanel({
  authKind,
  hasCredentials,
  onUpdate,
}: {
  authKind: string;
  hasCredentials: boolean;
  onUpdate: () => void;
}) {
  const what =
    authKind === 'api_token_with_subdomain'
      ? 'API token + company subdomain'
      : 'API token';
  return (
    <CredentialsSection
      title="Credentials"
      headerRight={
        <span
          className={`${toneClasses(hasCredentials ? 'ok' : 'warn')} rounded-md px-1.5 py-0.5 text-2xs`}
        >
          {hasCredentials ? 'configured' : 'not set'}
        </span>
      }
    >
      <div className="flex items-center justify-between gap-3 px-3 py-2.5 text-sm">
        <span className="text-muted-foreground">{what}</span>
        <Button type="button" variant="ghost" size="sm" onClick={onUpdate}>
          <Pencil size={14} />
          Update credentials
        </Button>
      </div>
    </CredentialsSection>
  );
}
