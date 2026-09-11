/**
 * A list with nothing in it, saying WHY and — where there is one — the
 * way out.
 *
 * "Nothing here" is false whenever a filter, a search or a scope is what
 * emptied the list, and it sends somebody hunting an old record away
 * believing it is gone.  So every empty list on this panel states the
 * constraint that emptied it, and offers the press that widens it back
 * when such a press exists.
 *
 * It is one component because the two panels had drifted: Live Map named
 * its constraint and offered "Clear filters", while Inventory's
 * search-emptied list was a muted sentence with no way out at all — the
 * same situation, in the same product, answered twice and unequally.
 * Live Map also carried this block's five layout properties twice over.
 */
import type { ReactNode } from 'react';

/** Both features list the same vehicles, so an account that has given
 *  this sign-in none is the same fact on both — said once, or the two
 *  panels explain one situation in two different ways. */
export const NO_VEHICLES_YET =
  'Your 4truck account has not given this sign-in any vehicles yet. '
  + 'Ask whoever manages your account to assign one, then reopen the panel.';

export default function EmptyState({ title, detail, action }: {
  title: string;
  /** The constraint, in the reader's terms — what narrowed this, or what
   *  the account has not given them yet. */
  detail?: ReactNode;
  /** The press that widens it, when widening is a press. */
  action?: ReactNode;
}) {
  return (
    // `role="status"`: the list emptying is the ANSWER to something the
    // person just typed or pressed, and a change nobody is told about is
    // a press that appears to have done nothing.
    <div role="status"
         style={{ padding: '20px 12px', display: 'grid', gap: 6, justifyItems: 'center', textAlign: 'center' }}>
      <strong style={{ fontSize: 13 }}>{title}</strong>
      {detail && <span className="muted small">{detail}</span>}
      {action}
    </div>
  );
}
