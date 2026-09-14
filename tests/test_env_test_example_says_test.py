"""The committed template for the TEST environment keeps saying so.

`.env.test.example` exists so a new developer sees, in the tree, which
values the test side of every service needs — and reads, before any of
them, that the API does not load this file, that the live database is
never its DATABASE_URL, and that the word TEST is there to make a value
copied into `.env` by mistake recognisable.  A template that loses that
header is a file of secrets with no explanation; this pins the header
and keeps the file committable (the `.env.*` ignore rule has to except
it by name).
"""

from __future__ import annotations

import subprocess

from tests._repo import REPO

TEMPLATE = REPO / ".env.test.example"


def test_the_template_exists_and_is_not_ignored():
    assert TEMPLATE.exists(), ".env.test.example is gone"
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env.test.example"], cwd=REPO).returncode == 0
    assert not ignored, ".gitignore's .env.* rule swallows the template — it needs the !.env.test.example exception"


def test_the_header_says_what_a_new_developer_must_know():
    head = "\n".join(TEMPLATE.read_text().splitlines()[:25])
    assert "TEST ENVIRONMENT" in head
    assert "NOT the file the API runs from" in head
    assert "never the live one" in head, "the database rule: the live plan rows hold live Price ids"
    assert "billing_preflight.sh" in head, "the one thing that reads this file"


def test_the_template_holds_no_secret_only_names_and_public_test_keys():
    for line in TEMPLATE.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        if not value:
            continue
        # one value may be written down: the mode word.  Even a published
        # test key goes in a comment — the commit guard cannot tell it
        # from a real one, and neither can the next reader
        assert key == "BILLING_PROVIDER", (
            f"{key} carries a value — the template names keys, it does not hold them")
