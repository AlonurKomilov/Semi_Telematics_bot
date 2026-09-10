"""Two rules the plan mask (roadmap step 6) will rely on, guarded ahead of it.

1. A plan name is never a gate.  No route, handler or service asks
   ``tier == "pro"``: when a plan withholds a feature, the resolver's
   mask says so through the permission, and every reader — nav, API,
   bot, AI — hears one answer.  The places that may say a plan name are
   the ones that DEFINE plans (billing), START one (signup's trial), or
   look at accounts from the operator's chair (the system console).
2. Billing never reads a feature flag.  It gates its own page on its own
   verb and tells the resolver "this account's plan is X"; whether X
   includes Maintenance is the resolver's business, not billing's.

Cross-layer, so it lives in the root suite.
"""

from __future__ import annotations

import os
import re

from tests._repo import REPO, is_test_path

PLAN_NAMES = ("free", "starter", "pro", "enterprise")

# A comparison against the tier — `tier == "pro"`, `.tier != 'free'`,
# `tier in ("pro", "enterprise")` — the shape a plan-as-gate takes.
_TIER_GATE = re.compile(
    r"\btier\b\s*(?:==|!=|\bin\b|\bnot in\b)\s*[\(\[\"']", re.I)

# Where a plan name may be spoken, and why.
_MAY_NAME_A_PLAN = {
    "capabilities/platform/billing/": "defines the plans",
    "adapters/storage/billing.py": "the plan table (prices, included units)",
    "interfaces/api/routes/system.py": "the operator console filters accounts by plan",
    "interfaces/api/auth.py": "signup starts the trial on a named plan",
    # Quotas by plan live here until step 6 folds them into the mask —
    # a lookup by plan for a NUMBER (seats, companies), not a gate on a feature.
    "interfaces/api/deps.py": "QUOTA_MAX_* by plan — a number, not a feature gate (step 6 moves it)",
    "features/settings/account/config.py": "shows the account's plan on the page",
}

_ROOTS = ("capabilities", "features", "interfaces/api", "interfaces/bot", "infra")


def _python_files():
    for root in _ROOTS:
        for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, root)):
            dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
            for f in filenames:
                if f.endswith(".py"):
                    p = os.path.join(dirpath, f)
                    if not is_test_path(p) and "migrations" not in f:
                        yield os.path.relpath(p, REPO)


def test_no_route_or_service_gates_on_a_plan_name():
    offenders = []
    for rel in _python_files():
        if any(rel.startswith(k) for k in _MAY_NAME_A_PLAN):
            continue
        src = open(os.path.join(REPO, rel), encoding="utf-8").read()
        for i, line in enumerate(src.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if _TIER_GATE.search(line) and any(f'"{p}"' in line or f"'{p}'" in line for p in PLAN_NAMES):
                offenders.append(f"{rel}:{i}: {line.strip()[:90]}")
    assert not offenders, (
        "a plan name used as a gate — the plan mask in the resolver is the one "
        "place a plan withholds a feature; ask the permission instead:\n  "
        + "\n  ".join(offenders))


_BILLING = "capabilities/platform/billing"
_FLAG_READS = re.compile(
    r"\bFeatureSet\b|\bROLE_PERMISSIONS\b|\bget_permissions\(|\bget_account_permissions\(|"
    r"\bget_user_permissions\(|\bcan_for_account\(|(?<![\w.])can\(|\bholds\(|\beffective_perms\(")
_ITS_OWN_GATE = ("can_manage_billing",)


def test_billing_never_reads_a_feature_flag():
    offenders = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO, _BILLING)):
        dirnames[:] = [d for d in dirnames if d != "tests" and not d.startswith("__")]
        for f in filenames:
            if not f.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, f), REPO)
            src = open(os.path.join(dirpath, f), encoding="utf-8").read()
            for i, line in enumerate(src.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if _FLAG_READS.search(line):
                    offenders.append(f"{rel}:{i}: {line.strip()[:90]}")
                for flag in re.findall(r"\bcan_[a-z_]+\b", line):
                    if flag not in _ITS_OWN_GATE:
                        offenders.append(f"{rel}:{i}: reads {flag}")
    assert not offenders, (
        "billing reads a feature flag — billing tells the resolver the plan; "
        "the resolver decides what the plan includes:\n  " + "\n  ".join(offenders))
