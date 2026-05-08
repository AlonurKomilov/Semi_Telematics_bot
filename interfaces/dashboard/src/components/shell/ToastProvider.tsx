import { toast as sonnerToast } from 'sonner';

type Tone = 'success' | 'error' | 'info' | 'warning';

interface NotifyFn {
  (message: string, tone?: Tone): void;
}

const dispatch: NotifyFn = (message, tone = 'success') => {
  if (tone === 'success') sonnerToast.success(message);
  else if (tone === 'error') sonnerToast.error(message);
  else if (tone === 'warning') sonnerToast.warning(message);
  else sonnerToast.info(message);
};

/**
 * Tiny shell hook that exposes a stable `notify(message, tone)` API.
 * Backed by the existing sonner Toaster mounted in main.tsx so it works
 * without a new provider tree.
 */
export function useToast(): { notify: NotifyFn } {
  return { notify: dispatch };
}

/** Imperative call site — useful inside service helpers. */
export function emitToast(message: string, tone: Tone = 'success') {
  dispatch(message, tone);
}
