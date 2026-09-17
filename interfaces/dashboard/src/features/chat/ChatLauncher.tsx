import { Link, useLocation } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Tip } from '@/components/tooltip';
import { CATALOG_BY_PATH } from '@/config/featureCatalog';
import { useViewPermissions } from '@/hooks/useViewPermissions';

const chat = CATALOG_BY_PATH.get('/chat')!;
const ChatIcon = chat.icon;

/** The same catalog grant and active-view resolver as the sidebar. */
export function ChatLauncher() {
  const { t } = useTranslation();
  const { hasAny } = useViewPermissions();
  const { pathname } = useLocation();
  const grants = Array.isArray(chat.permission) ? chat.permission : [chat.permission!];
  if (!hasAny(...grants)) return null;
  const active = pathname === chat.path || pathname.startsWith(`${chat.path}/`);
  const label = t(chat.labelKey);
  return (
    <Tip label={label}>
      <Button
        render={<Link to={chat.path} />}
        nativeButton={false}
        role="link"
        variant="ghost"
        size="icon"
        aria-label={label}
        aria-current={active ? 'page' : undefined}
        className={active ? 'bg-primary/10 text-primary' : 'text-muted-foreground'}
      >
        <ChatIcon aria-hidden />
      </Button>
    </Tip>
  );
}
