"""The words a customer reads about their own money.

Three things went out to customers that should not have. "Subscription
page" is a label this product tried in July and reverted the same week —
the customer-facing name is Billing, and four notifications still used
the retired one. "Thanks for keeping your fleet on 4truck" named a ROLE
where it meant their trucks. And "comp window" is what we call it to
each other, not what a customer would ever call it.

None of that is a bug in the ordinary sense; all of it is the product
speaking in a voice the reader does not share, on the one page where
being trusted matters most. So the rules are pinned here rather than
remembered.
"""

from __future__ import annotations

import re
from pathlib import Path

from capabilities.platform.billing import notifications

SOURCE = Path(notifications.__file__).read_text()

#: The docstring at the top of the module is a map for the next reader,
#: written in our words on purpose. Only the message BODIES are customer
#: copy, so only they are checked.
BODIES = SOURCE[SOURCE.index('"""', SOURCE.index('"""') + 3):]


def _messages() -> str:
    return BODIES


def test_the_retired_label_is_not_shown_to_customers():
    """The page is called Billing. "Subscription" was tried on
    2026-07-10 and reverted on 2026-07-13 — it read as its own feature
    and blurred the line with Driver Pay."""
    hits = re.findall(r"Subscription page", _messages())
    assert not hits, (
        f"{len(hits)} notification(s) still send customers to the "
        '"Subscription page" — the page is called Billing')


def test_no_customer_message_calls_their_trucks_a_fleet():
    """"fleet" is a live role id here (fleet.4truck.us, the Fleet row in
    the permission matrix), so it names that role and nothing else."""
    hits = [m.group(0) for m in re.finditer(r"[^\n]*\byour fleet\b[^\n]*", _messages(), re.I)]
    assert not hits, "a customer message calls their vehicles a fleet:\n    " + "\n    ".join(hits)


def test_no_internal_shorthand_reaches_a_customer():
    """"comp window" is ours. A customer has a complimentary period."""
    hits = [m.group(0).strip() for m in re.finditer(r'"[^"]*\bcomp window\b[^"]*"', _messages(), re.I)]
    assert not hits, "internal shorthand in a customer message:\n    " + "\n    ".join(hits)


def test_the_billing_page_offers_one_way_to_reach_a_human():
    """Two labels for the same act ("Talk to sales" beside "Contact us")
    is two things to learn for one action."""
    page = (Path(notifications.__file__).parents[3]
            / "interfaces" / "dashboard" / "src" / "features" / "billing" / "Billing.tsx").read_text()
    assert "Talk to sales" not in page, "the old second label is back"
    assert "Contact Sales" in page


def test_the_customer_page_does_not_name_our_own_machinery():
    """"the stub" is the name of a provider class in this repo. A
    customer reading their bill has no idea what a stub is."""
    page = (Path(notifications.__file__).parents[3]
            / "interfaces" / "dashboard" / "src" / "features" / "billing" / "Billing.tsx").read_text()
    for word in ("billing provider is the stub", "snapshots are recorded"):
        assert word not in page, f"machinery language on the customer page: {word!r}"
