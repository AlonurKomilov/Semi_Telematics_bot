"""No API route or feature service asks the role's built-in default.

``can(role, flag)`` resolves the account-aware FeatureSet only inside
the bot, whose auth primes a contextvar; everywhere else it returns
the hardcoded seed — and silently ignores every account-level layer:
the owner's matrix edit, a disabled department, the plan mask that
billing will write.  The API reads ``interfaces.api.deps.holds`` /
``effective_perms`` (the gate's own answer) instead.

Found the hard way twice: a driver-PII wall in the drivers router, then
eleven more sites (overview, maintenance, inspections).  Token-based on
purpose — a docstring that MENTIONS ``can(`` is not a call.
"""

from __future__ import annotations

import ast
import io
import os
import tokenize
from pathlib import Path

os.environ.setdefault("ENCRYPTION_KEY", "")

from tests._repo import REPO

#: where the API and the features live; the bot is exempt (primed), and
#: the permissions package owns ``can`` itself.
_ROOTS = ("features", "capabilities", "interfaces/api")
_EXEMPT_PREFIX = ("capabilities/permissions/",)


def _bare_can_calls(path: Path) -> list[int]:
    """Lines that reach the role-default ``can``: an import of it from
    the permissions package under ANY name (the alias is the usual
    disguise — ``import can as _can``), plus a direct call of the bare
    name for a file that got it some other way."""
    src = io.open(path, encoding="utf-8").read()
    lines: list[int] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return lines
    module_names: set[str] = set()      # names bound to the roles MODULE
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and (
                node.module == "capabilities.permissions"
                or node.module.startswith("capabilities.permissions.roles")):
            for alias in node.names:
                if alias.name == "can":
                    lines.append(node.lineno)
                if alias.name == "roles":
                    module_names.add(alias.asname or alias.name)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "capabilities.permissions.roles":
                    module_names.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        # the other disguise: ``roles.can(...)`` through a module binding
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "can" and isinstance(node.func.value, ast.Name)
                and node.func.value.id in module_names):
            lines.append(node.lineno)
    toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    for i, t in enumerate(toks):
        if (t.type == tokenize.NAME and t.string == "can"
                and i + 1 < len(toks) and toks[i + 1].string == "("
                and not (i > 0 and toks[i - 1].string in (".", "def"))):
            lines.append(t.start[0])
    return sorted(set(lines))


def _sources():
    for root in _ROOTS:
        for p in (Path(REPO) / root).rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            if "/tests/" in rel or rel.startswith(_EXEMPT_PREFIX):
                continue
            yield rel, p


def test_no_bare_can_outside_the_bot():
    hits = [f"{rel}:{n}" for rel, p in _sources() for n in _bare_can_calls(p)]
    assert not hits, (
        "bare can(role, flag) in the API — it reads the role's built-in "
        "default and ignores the account (matrix edits, disabled "
        "departments, the plan).  Read deps.holds / effective_perms:\n  "
        + "\n  ".join(hits))


def test_the_scan_sees_a_call_when_there_is_one(tmp_path):
    # The guard must not pass vacuously: a file with a real call is red,
    # a file that only mentions the name in a docstring is not.
    p = tmp_path / "x.py"
    p.write_text('def f(role):\n    """bare can() is bad"""\n    return can(role, "x")\n')
    assert _bare_can_calls(p) == [3]
    p.write_text('def f(role):\n    """bare can() is bad"""\n    return roles.can(role, "x")\n')
    assert _bare_can_calls(p) == []
    # the disguise: an aliased import is the same read
    p.write_text('from capabilities.permissions.roles import can as _can\n\ndef f(role):\n    return _can(role, "x")\n')
    assert _bare_can_calls(p) == [1]
    # the other disguise: the module under a name, then ``.can(``
    p.write_text('from capabilities.permissions import roles as R\n\ndef f(role):\n    return R.can(role, "x")\n')
    assert _bare_can_calls(p) == [4]
    p.write_text('import capabilities.permissions.roles as rm\n\ndef f(role):\n    return rm.can(role, "x")\n')
    assert _bare_can_calls(p) == [4]
    # a DEFINITION named can is not a call
    p.write_text('class X:\n    def can(self, flag):\n        return True\n')
    assert _bare_can_calls(p) == []
