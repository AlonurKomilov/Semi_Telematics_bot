"""Every account_settings key has a declared owner.

``PUT /settings`` accepted ANY key with ``can_manage_account`` — the
General settings feature's Manage action, described to an owner as
"timezone, bot + forum routing".  ``account_settings`` is shared, and
Storage and Integrations are PEER features in the same Administration
band, so that one grant reached:

    object_storage.backend            where every driver document lands
    object_storage.gdrive.*           a live OAuth credential
    kpi_thresholds                    Config · account-wide
    dqf.export_passphrase             what protects applicants' SSNs

Nobody grants that when they tick "General settings".  These tests hold
the fix in place, and the important one is
``test_no_undeclared_keys_are_written``: it fails when code writes a key
the registry does not know about, so adding a setting forces a decision
about who owns it rather than defaulting to whoever happens to be calling.
"""

from __future__ import annotations

import os
import re
import sys

os.environ.setdefault("ENCRYPTION_KEY", "")

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from capabilities.config import (  # noqa: E402
    SELF_ONLY, SETTING_OWNERS, SYSTEM_ONLY, owner_for,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The ONE permission an account_settings key may name.  Not a convenience
# constant — it is the assertion: account scope has exactly one owner.
ALLOWED_OWNER = "can_manage_config_all"


class TestOwnership:
    def test_every_live_key_shape_is_declared(self):
        """The keys actually present in production must all resolve."""
        for key in (
            "timezone",
            "ai_model", "ai_location",
            "forum_ai.camera", "forum_ai.parking",
            "object_storage.backend",
            "object_storage.gdrive.refresh_token",
            "object_storage.gdrive.root_folder_id",
            "object_storage.gdrive.user_email",
            "user:1527638770:ai_tier",
            "vehicle_field_precedence",
            "kpi_thresholds",
            "dqf.export_passphrase",
        ):
            assert owner_for(key) is not None, f"{key} has no declared owner"

    def test_undeclared_key_is_refused_not_defaulted(self):
        """None means REFUSE.  Defaulting to the caller's permission is
        exactly how one feature came to write another's config."""
        assert owner_for("something_nobody_declared") is None
        assert owner_for("") is None

    def test_no_key_claims_a_FEATURE_permission(self):
        """THE guard.  View / Manage / Config are three separate actions.

        ``account_settings`` is the CONFIG column's account-wide store —
        config.md's own table says so — so no row in it may be owned by a
        feature's Manage or View action.  The first version of this
        registry preserved each writer's existing permission, which made
        ownership visible but ENCODED the mixing: ``can_manage_storage``
        owned the backend choice, ``can_manage_integrations`` owned
        provider precedence, ``can_manage_account`` owned the rest.  Five
        permissions over one store, when the architecture says one.

        A feature's Manage keeps its real work — connecting Drive,
        retrying a sync — and stops owning the VALUES a computation reads.
        """
        offenders = [
            (r.key, r.permission) for r in SETTING_OWNERS
            if r.permission not in (ALLOWED_OWNER, SYSTEM_ONLY, SELF_ONLY)
        ]
        assert not offenders, (
            "account_settings keys owned by a feature permission rather than "
            f"{ALLOWED_OWNER}: {offenders}.\n"
            "Config is not Manage.  If the key is genuinely a feature's "
            "OPERATIONAL state and not a setting, it does not belong in "
            "account_settings at all — give it a typed column."
        )

    def test_the_two_outliers_are_the_only_non_config_kinds(self):
        """Exactly two things are not account config, and both are
        declared so the misplacement is VISIBLE rather than invisible:

          ai_*   — AI is a SERVICE; no account-level action exists to grant.
          user:* — per-user state, which config.md puts in the preferences
                   service, not here.
        """
        for r in SETTING_OWNERS:
            if r.kind == "config":
                continue
            assert r.key.startswith(("ai_model", "ai_location", "ai_vision", "user:")), (
                f"{r.key} is kind={r.kind!r} but is neither an AI model pin "
                "nor a per-user key — every other account_settings row is "
                "account-scope config"
            )

    def test_config_family_members_require_the_config_permission(self):
        for key in ("kpi_thresholds", "dqf.export_passphrase"):
            rule = owner_for(key)
            assert rule.permission == "can_manage_config_all"
            assert rule.kind == "config"

    def test_ai_model_has_no_account_permission(self):
        """AI is a SERVICE — services render "nothing to grant" because
        their access is derived.  There is no can_manage_ai."""
        rule = owner_for("ai_model")
        assert rule.permission == SYSTEM_ONLY
        assert rule.kind == "system"

    def test_per_user_keys_are_not_account_writable(self):
        rule = owner_for("user:999:ai_tier")
        assert rule.permission == SELF_ONLY
        assert rule.kind == "preference"

    def test_specific_rules_win_over_prefixes(self):
        """The Drive token is declared exactly AND covered by a prefix;
        first-match ordering must give the exact rule."""
        exact = owner_for("object_storage.gdrive.refresh_token")
        assert "credential" in exact.note.lower(), (
            "the exact rule lost to the object_storage.* prefix — reorder "
            "SETTING_OWNERS most-specific-first"
        )


class TestRegistryIsWellFormed:
    def test_no_duplicate_keys(self):
        keys = [r.key for r in SETTING_OWNERS]
        assert len(keys) == len(set(keys))

    def test_every_rule_names_an_owning_feature(self):
        for r in SETTING_OWNERS:
            assert r.feature, f"{r.key} declares no owning feature"

    def test_kinds_are_from_the_known_set(self):
        for r in SETTING_OWNERS:
            assert r.kind in ("manage", "config", "system", "preference"), (
                f"{r.key} has kind {r.kind!r}"
            )


class TestNoUndeclaredWrites:
    """THE interlock: code may not write a key the registry cannot place."""

    def test_no_undeclared_keys_are_written(self):
        """Resolves CONSTANTS too, not just literals.

        A first version matched only ``set_account_setting(acct, "key", …)``
        and scanned five keys — all of them AI.  Every writer that caused
        the original problem passes a module constant
        (``OBJECT_STORAGE_BACKEND_KEY``, ``KPI_SETTING_KEY``,
        ``DQF_PASSPHRASE_KEY``), so the test sailed past storage, KPI, DQF
        and integrations: green, and blind to the whole point.
        """
        pat = re.compile(
            r"set_account_setting\(\s*[^,]+,\s*(?:[\"']([^\"']+)[\"']|([A-Za-z_][\w.]*))",
        )
        const = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*[\"']([^\"']+)[\"']", re.M)
        skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv", "tests"}
        undeclared: list[str] = []
        consts: dict[str, str] = {}
        sources: list[tuple[str, str, bool]] = []

        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                # Constants defined ANYWHERE in the repo, so a writer that
                # imports its key from another module still resolves.
                consts.update(dict(const.findall(text)))
                for literal, name in pat.findall(text):
                    if literal:
                        sources.append((os.path.relpath(path, REPO), literal, True))
                    elif name:
                        sources.append((os.path.relpath(path, REPO), name, False))

        for rel, key, is_literal in sources:
            if is_literal:
                resolved = key
            else:
                # A NAME only counts when it resolves to a declared
                # constant.  Anything else — ``body.key``, a local, an
                # f-string fragment — is a RUNTIME value this test cannot
                # see, and guessing at it produces noise that trains
                # people to ignore the failure.  The generic endpoint's
                # ``body.key`` is precisely such a case, and it is
                # enforced at runtime by owner_for() instead.
                resolved = consts.get(key)
                if resolved is None:
                    continue
            if "{" in resolved:
                continue    # f-string; the prefix rules cover these
            if owner_for(resolved) is None:
                undeclared.append(f"{rel}: {key} → {resolved}")

        assert not undeclared, (
            "these account_settings keys are written but declare no owner:\n  "
            + "\n  ".join(sorted(set(undeclared)))
            + "\n\nAdd each to capabilities/config/account.py with the "
              "permission that owns it. Leaving one undeclared means "
              "PUT /settings refuses it — and means nobody decided whose it is."
        )


class TestReadableKeysAreAlsoWritable:
    """A setting you can SEE must be a setting you can SAVE.

    This class exists because per-key enforcement shipped and immediately
    broke five of them: ``account_name``, ``alert_defaults``, ``language``,
    ``digest_hour`` and ``scorecard_default_subject`` were read by the
    Settings page and refused by the new write guard — visible, editable
    in the UI, 422 on save.

    The drift test could not catch it.  That test scans write CALL SITES,
    and these keys are written through ``PUT /settings`` as a runtime
    ``body.key`` no static scan can resolve.  The only place their names
    appear literally is the GET handler's allow-list, so that is what this
    reads.  Two different blind spots need two different scans.
    """

    def _get_allowlist(self) -> list[str]:
        import re
        # config.py, not router.py: the config verbs moved out of the
        # feature router into the interface-layer pair beside it.
        path = os.path.join(
            REPO, "features", "settings", "account", "config.py",
        )
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        # The literal tuple the GET handler iterates.
        m = re.search(
            r"for key in \(\s*((?:[\"'][^\"']+[\"'],?\s*)+)\)", text,
        )
        assert m, "GET /settings no longer iterates a literal key tuple — update this test"
        return re.findall(r"[\"']([^\"']+)[\"']", m.group(1))

    def test_every_readable_key_has_a_declared_owner(self):
        missing = [k for k in self._get_allowlist() if owner_for(k) is None]
        assert not missing, (
            "these keys are returned by GET /settings but refused by "
            f"PUT /settings: {missing}\n"
            "A setting the UI shows and cannot save is worse than one it "
            "hides. Declare each in capabilities/config/account.py."
        )

    def test_every_key_the_code_READS_is_declared(self):
        """A key that is read but undeclared can never be SET.

        Third blind spot, after write-call-sites and the GET allow-list.
        ``scorecard_alert_drop_threshold`` and its floor twin are read by
        capabilities/scorecards/jobs.py, whose comment says "Accounts can
        override these via account_settings" — and no code writes them, so
        the only route is PUT /settings.  Undeclared, that documented
        override was impossible.

        Readers are where an unsettable setting shows up, because the
        feature that consumes a value is usually not the surface that
        sets it.
        """
        import re
        pat = re.compile(
            r"get_account_setting\(\s*[^,]+,\s*(?:[\"']([^\"']+)[\"']|([A-Z][A-Z0-9_]*))",
        )
        const = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*[\"']([^\"']+)[\"']", re.M)
        skip = {".git", "node_modules", "__pycache__", "venv", ".venv", "tests"}
        consts: dict[str, str] = {}
        seen: list[tuple[str, str, bool]] = []
        for root, dirs, files in os.walk(REPO):
            dirs[:] = [d for d in dirs if d not in skip]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
                consts.update(dict(const.findall(text)))
                for literal, name in pat.findall(text):
                    if literal or name:
                        seen.append(
                            (os.path.relpath(path, REPO), literal or name, bool(literal)),
                        )
        undeclared = []
        for rel, key, is_literal in seen:
            resolved = key if is_literal else consts.get(key)
            if not resolved or "{" in resolved:
                continue
            if owner_for(resolved) is None:
                undeclared.append(f"{rel}: {resolved}")
        assert not undeclared, (
            "these keys are READ but declare no owner, so nothing can set "
            "them:\n  " + "\n  ".join(sorted(set(undeclared)))
        )

    def test_the_allowlist_is_not_empty(self):
        """Guards the regex above: a silently-empty list would make the
        test above vacuously pass, which is how a check stops checking."""
        assert len(self._get_allowlist()) >= 5


class TestDedicatedWritersHonourTheDeclaredOwner:
    """The registry governed ONE door, and there are several.

    ``owner_for()`` is enforced inside ``PUT /settings``.  But most
    settings are not written there — they have their own endpoint, and
    that endpoint carries its own ``require_permission``.  So the
    registry could say ``object_storage.backend`` is
    ``can_manage_config_all`` while ``POST /object-storage/backend``
    happily wrote it on ``can_manage_storage``, and every existing test
    stayed green: the key WAS declared, the declaration simply had no
    force on that path.

    Three endpoints were in exactly that state — the storage backend
    switch, the vehicle source-precedence PUT, and the two forum-routing
    writes.  This test closes the door by checking the gate on the same
    function that performs the write.
    """

    # Endpoint functions that write an account_settings key, and the
    # permission the registry says owns that key.  Listed explicitly
    # rather than discovered: the point is to state the intended pairing
    # so a future regate has to argue with a name, not a regex.
    WRITERS = (
        ("capabilities/object_storage/config.py", "put_config"),
        ("features/vehicles/config.py", "put_config"),
        ("features/vehicles/config.py", "get_config"),
        ("capabilities/notifications/delivery_admin/config.py",
         "put_config"),
        ("capabilities/notifications/delivery_admin/config.py",
         "set_forum_subtypes"),
        ("features/kpi/config.py", "get_config"),
        ("features/kpi/config.py", "put_config"),
    )

    def test_config_endpoints_require_the_config_permission(self):
        import ast as _ast
        offenders: list[str] = []
        for rel, func in self.WRITERS:
            path = os.path.join(REPO, rel)
            src = open(path, encoding="utf-8").read()
            tree = _ast.parse(src)
            found = None
            for node in _ast.walk(tree):
                if isinstance(node, (_ast.AsyncFunctionDef, _ast.FunctionDef)) \
                        and node.name == func:
                    found = node
                    break
            assert found is not None, f"{rel}: no function named {func}"
            # The gate may be inline or a module-level dependency alias;
            # resolve the alias by looking it up in the same file.
            seg = _ast.get_source_segment(src, found) or ""
            perms = set(re.findall(
                r'require_permission[_a-z]*\(\s*"([a-z_]+)"', seg))
            if not perms:
                for alias in re.findall(r"Depends\((_[a-z_]+)\)", seg):
                    perms |= set(re.findall(
                        rf'^{alias}\s*=\s*require_permission[_a-z]*\(\s*"([a-z_]+)"',
                        src, re.M))
            if perms != {ALLOWED_OWNER}:
                offenders.append(f"{rel}::{func} gated on {sorted(perms) or '?'}")
        assert not offenders, (
            "config endpoints not on " + ALLOWED_OWNER + ":\n  "
            + "\n  ".join(offenders)
            + "\nA feature's Manage may operate the feature; it may not "
              "write the account-wide values a computation reads."
        )
