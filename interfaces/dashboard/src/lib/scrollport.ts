/**
 * Scroll the box an element actually lives in — never the boxes above it.
 *
 * `Element.scrollIntoView()` scrolls EVERY scrollable ancestor, and in
 * this app most of them are `overflow: hidden`: the shell, the chrome
 * envelope, the content card. Hidden does not mean unscrollable — it
 * means the user cannot scroll it back. So one call to bring a section
 * into view could push the whole shell up by the height of the header,
 * and nothing short of a reload brought it down again. That is exactly
 * what opening `/profile#modifications` did.
 *
 * These helpers find the one box that is MEANT to scroll — the nearest
 * ancestor whose overflow is `auto` or `scroll` and whose content
 * actually overflows — and move that alone. When there is none, they do
 * nothing: scrolling the document is the bug, not the fallback.
 */

/** The marker `AppShell` puts on the content scroller, so a caller with
 *  no element in hand can still reach the page's own scrollport. */
export const SHELL_SCROLLPORT_ATTR = 'data-shell-scroll';

export function shellScrollport(): HTMLElement | null {
  return document.querySelector<HTMLElement>(`[${SHELL_SCROLLPORT_ATTR}]`);
}

/** The nearest ancestor that scrolls, or null when nothing does. */
export function scrollportOf(el: Element): HTMLElement | null {
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const { overflowY } = getComputedStyle(p);
    if ((overflowY === 'auto' || overflowY === 'scroll') && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

type Block = 'start' | 'center' | 'end' | 'nearest';

/**
 * `scrollIntoView`, kept to one box.
 *
 * `block: 'start'` honours the target's own `scroll-margin-top`, so a
 * section sitting under a sticky header still lands below it — the same
 * thing `scroll-mt-*` buys from the native call.
 */
export function scrollIntoScrollport(
  el: Element,
  { behavior = 'smooth', block = 'start' }: { behavior?: ScrollBehavior; block?: Block } = {},
): void {
  const port = scrollportOf(el);
  if (!port) return;
  const box = el.getBoundingClientRect();
  const view = port.getBoundingClientRect();
  const start = box.top - view.top + port.scrollTop;
  const margin = parseFloat(getComputedStyle(el).scrollMarginTop) || 0;

  let top: number;
  if (block === 'center') top = start - (port.clientHeight - box.height) / 2;
  else if (block === 'end') top = start - port.clientHeight + box.height;
  else if (block === 'nearest') {
    const above = box.top < view.top;
    const below = box.bottom > view.bottom;
    if (!above && !below) return;                       // already whole in view
    top = above ? start - margin : start - port.clientHeight + box.height;
  } else top = start - margin;

  top = Math.max(0, Math.min(top, port.scrollHeight - port.clientHeight));
  // `scrollTo` is the one that takes a behaviour; the assignment is the
  // fallback for environments that do not implement it (jsdom).
  if (typeof port.scrollTo === 'function') port.scrollTo({ top, behavior });
  else port.scrollTop = top;
}

/** Back to the top of the page's own scroller — what `window.scrollTo(0)`
 *  used to mean before the shell owned the viewport. */
export function scrollShellTop(behavior: ScrollBehavior = 'smooth'): void {
  const port = shellScrollport();
  if (!port) return;
  if (typeof port.scrollTo === 'function') port.scrollTo({ top: 0, behavior });
  else port.scrollTop = 0;
}
