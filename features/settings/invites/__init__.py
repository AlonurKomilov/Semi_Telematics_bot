"""Settings component · Invites — create / resend / revoke / extend account invitations."""

# Registers the team.invite_accepted notification category (and exposes
# announce_invite_accepted) — importing the feature is enough for the API
# process to know the category for the preferences UI.
from features.settings.invites import notifications  # noqa: F401,E402
