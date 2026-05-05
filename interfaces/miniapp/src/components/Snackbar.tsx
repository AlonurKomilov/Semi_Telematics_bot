// Lightweight transient toast / snackbar.  We deliberately do NOT depend
// on Telegram-UI's snackbar exports because the available export name
// changes between minor versions of the package — this keeps the build
// stable and matches the look of Telegram's native success/error toasts.

import { useEffect } from 'react';
import { haptics } from '../hooks/useTelegram';

export type SnackbarKind = 'success' | 'error' | 'info';

interface Props {
  open: boolean;
  text: string;
  kind?: SnackbarKind;
  onClose: () => void;
  /** Auto-dismiss after this many ms.  Set 0 to keep until close is called. */
  duration?: number;
}

export function Snackbar({ open, text, kind = 'info', onClose, duration = 2200 }: Props) {
  useEffect(() => {
    if (!open) return;
    if (kind === 'success') haptics.success();
    else if (kind === 'error') haptics.error();
    if (duration <= 0) return;
    const t = window.setTimeout(onClose, duration);
    return () => window.clearTimeout(t);
  }, [open, kind, duration, onClose]);

  if (!open) return null;
  return (
    <div className={`snackbar snackbar--${kind}`} role="status" aria-live="polite">
      <span className="snackbar__icon" aria-hidden>
        {kind === 'success' ? '✓' : kind === 'error' ? '⚠' : 'ℹ'}
      </span>
      <span className="snackbar__text">{text}</span>
    </div>
  );
}
