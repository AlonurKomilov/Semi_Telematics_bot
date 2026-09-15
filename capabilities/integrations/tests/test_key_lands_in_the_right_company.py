"""A working key is not a correct key.

Five carriers on one account, five API keys, five rows on the
Integration card. Nothing automatic can put one company's key against
another — the builder never lends a key. A PERSON can, by pasting into
the wrong row, and that mistake is the worst shape available: the probe
goes green, the card says healthy, and one carrier's drivers are
reported under another carrier's name on a page about hours of service.

Nothing else in the chain can catch it, because nothing else knows what
the operator MEANT. The row does.

``companies.usdot_number`` is described in the model as "the stable
join key for matching integration records to this sub-company", and
ORIENT's ``/api/companies/info`` returns the carrier's DOT number — so
the per-company probe can compare the two and refuse.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from adapters.telematics.protocol import ConnectionStatus
from capabilities.integrations.shared.helpers import (
    cross_check_company_identity as _cross_check,
)


def _company(code="PTG", usdot="3688109"):
    return SimpleNamespace(code=code, display_name="PREMIER TRUCKING GROUP",
                           usdot_number=usdot)


def _ok(dot="3688109", kind="usdot"):
    return ConnectionStatus(
        ok=True, message=f"connected to CARRIER (DOT {dot})",
        provider_account_id=dot, provider_account_id_kind=kind,
    )


# ── The mistake this exists for ───────────────────────────────────

def test_a_valid_key_in_the_wrong_row_is_refused():
    ok, message = _cross_check(_ok(dot="1234567"), _company(usdot="3688109"),
                               "PTG")
    assert ok is False
    assert "1234567" in message and "3688109" in message
    assert "wrong row" in message, (
        "the operator must be told the key is fine and the ROW is wrong "
        "— otherwise they go looking for a bad key"
    )


def test_the_matching_key_passes_and_keeps_the_providers_own_words():
    ok, message = _cross_check(_ok(), _company(), "PTG")
    assert ok is True
    assert "connected to" in message


def test_a_leading_zero_is_not_a_mismatch():
    """DOT numbers get stored with and without padding. Refusing a
    correct key over a zero would train the operator to ignore this."""
    ok, _ = _cross_check(_ok(dot="0003688109"), _company(usdot="3688109"),
                         "PTG")
    assert ok is True


# ── When it must hold its tongue ──────────────────────────────────

def test_a_provider_whose_id_means_nothing_to_us_is_not_judged():
    """Samsara's account id is an org id and matches nothing we store.
    An empty kind is NO OPINION, never a mismatch."""
    ok, _ = _cross_check(_ok(dot="org-4815", kind=""), _company(), "PTG")
    assert ok is True


def test_a_company_with_no_usdot_on_file_is_not_punished():
    """Refusing a good key because a company row has a blank field
    would make the guard the problem."""
    ok, _ = _cross_check(_ok(), _company(usdot=""), "PTG")
    assert ok is True


def test_a_provider_that_returns_no_id_is_not_punished():
    ok, _ = _cross_check(_ok(dot=""), _company(), "PTG")
    assert ok is True


def test_an_unknown_company_row_is_not_punished():
    """The lookup is best-effort — a tenant DB hiccup must not turn a
    working key into a failure."""
    ok, _ = _cross_check(_ok(), None, "PTG")
    assert ok is True


# ── A failed probe stays failed ───────────────────────────────────

def test_a_rejected_key_is_not_rescued_by_the_cross_check():
    bad = ConnectionStatus(ok=False, message="API key rejected by ORIENT ELD")
    ok, message = _cross_check(bad, _company(), "PTG")
    assert ok is False
    assert "rejected" in message


# ── The declaration itself ────────────────────────────────────────

def test_orient_declares_its_account_id_is_a_usdot():
    """Without the declaration the comparison never runs, and the guard
    is dead code that still looks alive."""
    import inspect

    from adapters.telematics.orient_eld.provider import OrientEldProvider

    src = inspect.getsource(OrientEldProvider.test_connection)
    assert 'provider_account_id_kind="usdot"' in src


def test_the_kind_defaults_to_no_opinion():
    """Every provider that has not thought about this must be silently
    exempt, not silently wrong."""
    assert ConnectionStatus(ok=True).provider_account_id_kind == ""


# ── The form must ask the question at all ─────────────────────────

def test_a_per_company_provider_declares_that_its_keys_are_scoped():
    """The connect form reads this to decide whether to ask WHICH
    company. Without the declaration it asks for a token alone, stores
    it as account-wide, and four of five carriers are silently
    unconnected on a page about hours of service."""
    from adapters.telematics.catalog import PROVIDER_CATALOG

    assert PROVIDER_CATALOG["orient_eld"].credential_scope == "company"


def test_an_account_scoped_provider_keeps_the_simple_form():
    """Samsara genuinely supports one token covering every company —
    asking which one would be a question with no right answer."""
    from adapters.telematics.catalog import PROVIDER_CATALOG

    assert PROVIDER_CATALOG["samsara"].credential_scope == "account"
    assert PROVIDER_CATALOG["datatruck"].credential_scope == "account"


def test_the_scope_reaches_the_dashboard():
    """Declared in the catalog and never serialized is the same as not
    declared — the form only sees what GET /integrations sends."""
    from adapters.telematics.catalog import PROVIDER_CATALOG
    from capabilities.integrations.shared.helpers import (
        serialize_catalog_entry,
    )

    out = serialize_catalog_entry(PROVIDER_CATALOG["orient_eld"])
    assert out["credential_scope"] == "company"
    assert serialize_catalog_entry(
        PROVIDER_CATALOG["samsara"])["credential_scope"] == "account"


def test_a_company_scoped_connect_writes_straight_into_the_companies_map():
    """What the form posts must be what the builder reads. There is no
    account-level slot for a company-scoped key, and writing one would
    leave a live credential behind that nothing ever reads."""
    from adapters.telematics.orient_eld.client import (
        build_multi_company_orient_client,
    )

    posted = {"companies": {"PTG": "key-ptg"}}
    client = build_multi_company_orient_client(posted)
    assert client.company_codes == ["PTG"]
