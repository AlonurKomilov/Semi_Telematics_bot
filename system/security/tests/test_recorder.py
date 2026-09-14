"""The ledger keeps every refusal and every monitored request, and nothing else.

The policy is three small functions and one cache, and each has a way to
be wrong that would matter: recording customers' successful requests
(a surveillance table we did not agree to), storing a reset token from
an /api/auth query string, or raising on the request path and failing a
customer's call because we were writing about it.
"""

from __future__ import annotations

import inspect

import pytest

from system.security import recorder


@pytest.fixture(autouse=True)
def _fresh_cache():
    recorder.forget_security()
    yield
    recorder.forget_security()


# ── should_record ─────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 403, 429])
@pytest.mark.parametrize("security", [None, "normal", "monitored", "quarantined"])
def test_every_refusal_is_kept_whoever_sent_it(status, security):
    assert recorder.should_record(status, security) is True


@pytest.mark.parametrize("status", [200, 201, 204, 400, 404, 422, 500, 502])
@pytest.mark.parametrize("security", ["monitored", "quarantined"])
def test_a_watched_subject_is_kept_whatever_the_status(status, security):
    """Both watched standings record everything.  `quarantined` used to
    sit with the unwatched below — which would have meant a subject we
    decided to HOLD going quieter in the ledger than one we were merely
    observing.  It is the stronger standing; it records at least as
    much."""
    assert recorder.should_record(status, security) is True


@pytest.mark.parametrize("status", [200, 201, 204, 400, 404, 422, 500])
@pytest.mark.parametrize("security", [None, "normal"])
def test_nothing_else_is_kept(status, security):
    """A customer's 200 is their business; a scanner's 404 is noise."""
    assert recorder.should_record(status, security) is False


# ── safe_query ────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/reset-password",
                                  "/api/auth/verify-email", "/api/v1/auth/x"])
def test_auth_paths_never_keep_a_query_string(path):
    assert recorder.safe_query(path, "token=abc&email=a@b.c") is None


def test_other_paths_keep_a_bounded_query():
    long = "q=" + "x" * 1000
    kept = recorder.safe_query("/api/vehicles", long)
    assert kept is not None and len(kept) == recorder.QUERY_MAX
    assert recorder.safe_query("/api/vehicles", "") is None
    assert recorder.safe_query("/api/vehicles", None) is None


# ── the write path never takes a body ─────────────────────────────

def test_the_api_has_no_way_to_pass_a_body():
    """There is no column for one and no parameter for one.  A future
    "let's also keep the payload" must fail here first."""
    params = set(inspect.signature(recorder.record_request).parameters)
    for forbidden in ("body", "payload", "json", "data", "content"):
        assert forbidden not in params
    assert {"method", "path", "status"} <= params


# ── record_request against a fake platform db ─────────────────────

class _FakeAccount:
    def __init__(self, security): self.security = security


class _FakePlatform:
    def __init__(self, standings: dict[int, str | None], fail_write: bool = False):
        self._standings = standings
        self.fail_write = fail_write
        self.rows: list[dict] = []
        self.account_reads = 0

    async def get_account(self, account_id):
        self.account_reads += 1
        k = self._standings.get(account_id, "missing")
        return None if k == "missing" else _FakeAccount(k)

    async def record_security_request(self, **row):
        if self.fail_write:
            raise RuntimeError("db down")
        self.rows.append(row)


@pytest.fixture
def platform(monkeypatch):
    # account 1 is unremarkable, account 2 is watched — and either
    # could be a paying customer, which is the point of the split.
    fake = _FakePlatform({1: "normal", 2: "monitored"})
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_a_monitored_accounts_success_is_written_with_its_standing(platform):
    kept = await recorder.record_request(
        method="GET", path="/api/vehicles", status=200, account_id=2,
        user_id=31, role="owner", query="page=2", duration_ms=12,
        ip="87.192.238.227", ua="curl/8.19.0", request_id="r1")
    assert kept is True
    assert platform.rows == [{
        "method": "GET", "path": "/api/vehicles", "status": 200,
        "account_id": 2, "user_id": 31, "role": "owner", "security": "monitored",
        "query": "page=2", "duration_ms": 12, "ip": "87.192.238.227",
        "ua": "curl/8.19.0", "request_id": "r1"}]


@pytest.mark.asyncio
async def test_an_unwatched_accounts_success_is_not_written(platform):
    kept = await recorder.record_request(
        method="GET", path="/api/vehicles", status=200, account_id=1)
    assert kept is False
    assert platform.rows == []


@pytest.mark.asyncio
async def test_a_refusal_with_no_account_is_written(platform):
    """The probe's /system/* attempts carried no valid account at all."""
    kept = await recorder.record_request(
        method="GET", path="/api/system/accounts", status=403, account_id=None,
        ip="203.0.114.11")
    assert kept is True
    assert platform.rows[0]["account_id"] is None
    assert platform.rows[0]["security"] is None


@pytest.mark.asyncio
async def test_the_auth_query_is_dropped_even_for_a_monitored_account(platform):
    await recorder.record_request(
        method="POST", path="/api/auth/reset-password", status=200,
        account_id=2, query="token=SECRET123")
    assert platform.rows[0]["query"] is None


@pytest.mark.asyncio
async def test_ip_and_ua_are_bounded(platform):
    await recorder.record_request(
        method="GET", path="/api/x", status=403,
        ip="9" * 500, ua="u" * 5000)
    assert len(platform.rows[0]["ip"]) == recorder.IP_MAX
    assert len(platform.rows[0]["ua"]) == recorder.UA_MAX


@pytest.mark.asyncio
async def test_the_security_lookup_is_cached_for_a_minute(platform, monkeypatch):
    for _ in range(5):
        await recorder.record_request(method="GET", path="/api/x", status=200, account_id=2)
    assert platform.account_reads == 1

    # time passes past the TTL → one more read, not five
    import time as _t
    real = _t.monotonic()
    monkeypatch.setattr(recorder.time, "monotonic", lambda: real + recorder._SECURITY_TTL_S + 1)
    await recorder.record_request(method="GET", path="/api/x", status=200, account_id=2)
    assert platform.account_reads == 2


@pytest.mark.asyncio
async def test_a_failing_write_never_raises(platform):
    """Observation must not fail the customer's request."""
    platform.fail_write = True
    kept = await recorder.record_request(method="GET", path="/api/x", status=403)
    assert kept is False


@pytest.mark.asyncio
async def test_a_failing_security_lookup_never_raises_and_still_keeps_refusals(monkeypatch):
    class _Broken:
        async def get_account(self, _):
            raise RuntimeError("db down")
        async def record_security_request(self, **row):
            self.rows = getattr(self, "rows", []) + [row]
    broken = _Broken()
    monkeypatch.setattr("infra.platform.get_platform_db", lambda: broken)
    assert await recorder.record_request(method="GET", path="/api/x", status=403, account_id=7) is True
    assert await recorder.record_request(method="GET", path="/api/x", status=200, account_id=7) is False
