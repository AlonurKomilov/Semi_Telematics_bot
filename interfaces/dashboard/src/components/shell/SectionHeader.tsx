import type { ReactNode } from 'react';

import { cn } from '@/lib/utils';

/**
 * SectionHeader — the title of a section or a card, and whatever control
 * sits opposite it.
 *
 * `PageHeader` already owned the page level (`<h1>`, `text-2xl
 * font-bold`). Below it there was nothing, and 106 titles wrote
 * themselves: 6 type combos, 11 distinct size+margin pairs, and — the
 * part that is not a style problem — SIX different elements. 67 `<h2>`,
 * 7 `<h3>`, 4 `<h1>`, but also 13 `<p>`, 8 `<div>` and 7 `<span>`. A
 * quarter of this app's sections are missing from the heading outline a
 * screen-reader user navigates by, because a title styled like a heading
 * is not a heading.
 *
 * Two axes, deliberately separate:
 *   `size`  — the §4 visual ROLE: section (`text-lg`) or card
 *             (`text-base`). Both are `font-semibold`.
 *   `as`    — the heading LEVEL in the document outline. Defaults from
 *             `size`, but a card title directly under a page title is an
 *             `h2` even though it reads at card size. Conflating the two
 *             is how you get either a broken outline or improvised type.
 *
 * NO margin of its own. 43 of the 106 had none — they sit in a
 * `space-y-*` parent, which is the house pattern — so a primitive that
 * shipped `mb-3` would double-space the majority. The container owns the
 * gap below a title; the title owns its type.
 */
export default function SectionHeader({
  children,
  size = 'section',
  as,
  icon,
  description,
  action,
  className,
  id,
}: {
  children: ReactNode;
  size?: 'section' | 'card';
  as?: 'h1' | 'h2' | 'h3' | 'h4';
  /** Sits inside the heading, before the text. Five of the first
   *  eleven call sites had one, hand-spelled as `inline-flex
   *  items-center gap-2` on the title itself. */
  icon?: ReactNode;
  /** A muted line under the title. Omit it rather than pad it out. */
  description?: ReactNode;
  /** Sits opposite the title; the row becomes a flex justify-between. */
  action?: ReactNode;
  className?: string;
  id?: string;
}) {
  const Tag = as ?? (size === 'card' ? 'h3' : 'h2');
  const title = (
    <Tag
      id={id}
      className={cn(
        'text-foreground font-semibold',
        icon && 'inline-flex items-center gap-2',
        size === 'card' ? 'text-base' : 'text-lg',
        // `min-w-0` so a long title truncates instead of shoving the
        // action off the row — the failure that made several call sites
        // add `truncate` by hand.
        action && 'min-w-0 truncate',
      )}
    >
      {icon}
      {children}
    </Tag>
  );

  if (!action && !description) {
    return className ? <div className={className}>{title}</div> : title;
  }

  return (
    <div className={cn(action && 'flex items-center justify-between gap-4', className)}>
      <div className="min-w-0">
        {title}
        {description && (
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        )}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
