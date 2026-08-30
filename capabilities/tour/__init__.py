"""Tour — the backend half of interactive product tours.

The ENGINE is client-side by design (components/tour on the
dashboard): tours point at DOM elements, which is frontend knowledge,
and the client-first rule for dismissible advice is recorded in the
callouts capability's history.  What the backend owns is the one thing
the client cannot honestly know — behavioural signals.

A tour like "adding tasks one by one?" should be OFFERED to the person
who created six tasks individually this week and NEVER offered to the
person who already uses bulk-add.  Both facts live in activity_events.
The contract for reading them:

  * SELF-SCOPED, absolutely.  The endpoint aggregates the requesting
    user's OWN actions — there is no parameter for another user, no
    admin view, no "who is behind" report, and nothing is stored.  It
    lets a page ask "what have I done here?", never "what have they".
  * ALLOWLISTED.  Only the (entity_type, action) pairs named in
    ``ALLOWED_SIGNALS`` may be asked about, so the endpoint cannot be
    driven as a generic fishing API over the audit trail.

``solo`` vs ``grouped``: bulk operations write their trail events under
one group_id (see new_group_id in the activity_trail capability), so
grouped counts ARE the "already uses the bulk path" signal and solo
counts the "does it one at a time" signal.
"""

# Every signal a tour may ask about.  Adding a pair is a deliberate,
# reviewed act — capabilities/tour/tests enforces that the
# dashboard's tour data asks for nothing outside this set.
ALLOWED_SIGNALS: frozenset[tuple[str, str]] = frozenset({
    ("maintenance_task", "create"),
})
