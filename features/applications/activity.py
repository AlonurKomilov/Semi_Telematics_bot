"""Driver applications — activity-trail entity declaration.

Declares the entity type this feature writes and the permission that
gates viewing its per-record history.  One file, data only — the
retention-hub declaration pattern.

WHY AN APPLICATION NEEDS A TRAIL, specifically
----------------------------------------------
``update_application_status`` OVERWRITES ``status``, ``reviewed_by`` and
``reviewed_at``, so the row remembers only its latest transition.  A
hiring decision is exactly the kind of thing an FMCSA audit asks about
after the fact — when did this driver move to Approved, who approved
them, was anything reversed — and none of that was recoverable.

It also blocks the DQF sidecar written to the customer's own cloud: that
document can attest to the CURRENT stage, but without a trail it cannot
show how the application got there.  History has to exist before it can
be exported.

NO RESTORE, deliberately.  ``restore_permissions`` / ``restore_table`` are
unset, so the registry's fail-closed rule means an application cannot be
recreated from its trail — no button, no endpoint. Re-materialising a
submitted FMCSA application from diff rows would produce a record nobody
signed, carrying consents nobody gave. A withdrawn application should be
re-submitted by the applicant, not reconstructed by staff.
"""

from capabilities.activity_trail.registry import (
    EntityDescriptor,
    register_entity,
)

register_entity(EntityDescriptor(
    "driver_application", "Driver application", "applications",
    view_permissions=('can_manage_applications',),
    # Pre-consent PII, and more of it than the driver record holds: an
    # application carries DOB, SSN, address history and prior-employment
    # detail.  Masked in the trail the same way the drivers feature masks
    # its own — the history should show THAT a field changed and by whom,
    # not republish the value into a second place.
    sensitive_fields=frozenset([
        'dob', 'dob_enc', 'ssn', 'ssn_enc', 'ssn_hash',
        'email', 'phone', 'address_history_json', 'personal_json',
        'employment_json', 'incidents_json', 'recruiter_notes',
        'sig_name', 'submit_ip',
    ]),
))
