"""Finding a swallowed error, in TypeScript and in Python.

The scanner is its own module because two things use it: the guard in
``tests/test_errors_are_not_swallowed.py``, and anybody who wants the
current census without running pytest (``python3 -m tests._swallow``).

**What counts as a swallow.** A caught error whose handler does nothing
AND says nothing.  A handler with a comment is not a swallow — it is a
decision somebody wrote down, and this guard's whole point is to make
that the only legal form:

    } catch { /* the choice is lost, the session is not */ }   # fine
    } catch { }                                               # not
    .catch(() => {})                                          # not
    except Exception:                                         # not
        pass

**Why comments and strings are masked first.** A guard that reads prose
finds its own explanation and fails.  That happened twice in one day
here — a SQL guard matched the words "from Vehicles" in a sentence, and
the first draft of this scanner flagged the comment describing the very
bug it was written for.  So TypeScript is masked (comments and string
literals blanked) before any pattern runs, and the body is then read
from the ORIGINAL text to see whether a reason is present.
"""
from __future__ import annotations

import pathlib
import re

#: Never scanned: vendored code, build output, and archived scripts.
SKIP = ("node_modules", "/dist/", "/.git/", "/_archive/", "/venv/",
        "site-packages", "/scripts/archive/")


def mask_ts(src: str) -> str:
    """The source with comments and string literals blanked to spaces.

    Offsets are preserved, so a match in the mask points at the same
    place in the original.
    """
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i)); i = j
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i)); i = j
        elif c in "\"'`":
            q, j = c, i + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(" " * (j - i)); i = j
        else:
            out.append(c); i += 1
    return "".join(out)


def _match_brace(s: str, i: int) -> int:
    depth, j = 0, i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


_TS_CATCH = re.compile(r"\bcatch\s*(?:\(\s*[\w$]*\s*(?::\s*\w+)?\s*\))?\s*\{")
_TS_THEN = re.compile(r"\.catch\s*\(\s*\(\s*[\w$]*\s*\)\s*=>\s*\{")


def scan_ts(src: str) -> list[int]:
    """1-indexed lines of every handler that does nothing and says nothing."""
    masked = mask_ts(src)
    hits: list[int] = []
    for pattern in (_TS_CATCH, _TS_THEN):
        for m in pattern.finditer(masked):
            end = _match_brace(masked, m.end() - 1)
            if end < 0:
                continue
            if masked[m.end():end].strip():
                continue                      # it does something
            if src[m.end():end].strip():
                continue                      # it says something
            hits.append(src[:m.start()].count("\n") + 1)
    return sorted(hits)


def scan_py(src: str) -> list[int]:
    lines = src.splitlines()
    hits: list[int] = []
    for i, line in enumerate(lines):
        if re.match(r"^\s*except\b[^:]*:\s*pass\s*$", line):
            hits.append(i + 1)
            continue
        head = re.match(r"^(\s*)except\b[^:]*:\s*$", line)
        if not head:
            continue
        indent, said_why = len(head.group(1)), False
        for nxt in lines[i + 1:]:
            if not nxt.strip():
                continue
            if nxt.strip().startswith("#"):
                said_why = True
                continue
            if len(nxt) - len(nxt.lstrip()) <= indent:
                break
            if nxt.strip() == "pass" and not said_why:
                hits.append(i + 1)
            break
    return hits


def scan_file(path: pathlib.Path) -> list[int]:
    src = path.read_text(errors="ignore")
    if path.suffix in (".ts", ".tsx"):
        return scan_ts(src)
    if path.suffix == ".py":
        return scan_py(src)
    return []


def walk(root: pathlib.Path) -> dict[str, list[int]]:
    """Every scannable file under ``root`` that holds at least one."""
    found: dict[str, list[int]] = {}
    for p in root.rglob("*"):
        sp = str(p)
        if any(x in sp for x in SKIP):
            continue
        if p.suffix in (".ts", ".tsx"):
            if "/src/" not in sp:
                continue
        elif p.suffix != ".py":
            continue
        try:
            hits = scan_file(p)
        except OSError:
            continue
        if hits:
            found[str(p.relative_to(root))] = hits
    return found


if __name__ == "__main__":  # a census without pytest
    from tests._repo import REPO
    found = walk(REPO)
    total = sum(len(v) for v in found.values())
    print(f"{total} swallowed errors with no stated reason, in {len(found)} files")
    for f, hits in sorted(found.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(hits):>4}  {f}")
