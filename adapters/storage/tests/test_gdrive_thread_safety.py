"""The Drive client must never share its HTTP transport across threads.

googleapiclient's default transport is httplib2 — not thread-safe —
and the storage sync worker fans ``drive.put`` across OS threads
(asyncio.to_thread).  Sharing one client interleaved writes on one TLS
connection: SSL record errors on every upload AND glibc heap
corruption that hard-killed the bot ~75s after boot (2026-08-03..07).
The requestBuilder in ``_build_drive_service`` mints a fresh
AuthorizedHttp per request; this test pins that contract.
"""

from __future__ import annotations


def test_each_request_gets_its_own_transport(monkeypatch):
    monkeypatch.setenv("GDRIVE_OAUTH_CLIENT_ID", "test-client")
    monkeypatch.setenv("GDRIVE_OAUTH_CLIENT_SECRET", "test-secret")
    import google_auth_httplib2

    from adapters.storage.object_storage_gdrive import _build_drive_service

    svc = _build_drive_service("dummy-refresh-token")
    req1 = svc.files().list(q="x")
    req2 = svc.files().list(q="x")
    # Distinct transports (the thread-safety fix) …
    assert req1.http is not req2.http
    assert isinstance(req1.http, google_auth_httplib2.AuthorizedHttp)
    # … but ONE credentials object, so the account still performs a
    # single token exchange rather than one per request.
    assert req1.http.credentials is req2.http.credentials
