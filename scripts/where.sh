#!/usr/bin/env bash
# Where is the work?  Three numbers, and none of them are visible today.
#
#   ./scripts/where.sh            # report
#   ./scripts/where.sh --check    # exit 1 if anything is stranded (pre-flight)
#
# Two people edit this repo and production runs OUT OF IT, so "is it
# live?" and "is it committed?" are different questions that currently
# look identical.  On 2026-08-07 both were answered confidently and
# wrongly within an hour: the object-storage rename was complete in the
# working tree and running fine, while ten files on HEAD still imported
# the module it deleted — a fresh clone could not start.  Nothing
# surfaced the gap, because the tree is what runs.
#
# The three states, and what each one being non-zero means:
#
#   uncommitted  work that RUNS today but exists nowhere else.  A worker
#                recycle picks it up mid-save; a clone never sees it.
#   undeployed   only meaningful once a deploy checkout exists (see
#                DEPLOY_DIR).  Until then production IS the edit tree,
#                so this is structurally zero and says so.
#   unpushed     work that exists on this machine only.  One disk.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Set DEPLOY_DIR when a separate served checkout exists.  Absent, the
# script reports the truth rather than pretending the question applies.
DEPLOY_DIR="${DEPLOY_DIR:-/srv/4truck-api}"
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

cd "$REPO" || exit 1
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

# ── 1. uncommitted ────────────────────────────────────────────────
UNCOMMITTED="$(git status --porcelain | wc -l | tr -d ' ')"

# ── 2. undeployed ─────────────────────────────────────────────────
if [ -d "$DEPLOY_DIR/.git" ]; then
  DEPLOY_SHA="$(git -C "$DEPLOY_DIR" rev-parse HEAD 2>/dev/null)"
  UNDEPLOYED="$(git rev-list --count "${DEPLOY_SHA}..${BRANCH}" 2>/dev/null || echo '?')"
  DEPLOY_NOTE="$DEPLOY_DIR @ ${DEPLOY_SHA:0:8}"
else
  UNDEPLOYED="-"
  DEPLOY_NOTE="no deploy checkout — production serves the edit tree"
fi

# ── 3. unpushed ───────────────────────────────────────────────────
if git rev-parse --abbrev-ref "@{upstream}" >/dev/null 2>&1; then
  UNPUSHED="$(git rev-list --count "@{upstream}..HEAD")"
  UPSTREAM="$(git rev-parse --abbrev-ref '@{upstream}')"
else
  UNPUSHED="?"; UPSTREAM="(no upstream)"
fi

printf '\n  branch %s\n\n' "$BRANCH"
printf '  uncommitted  %-4s  files   — runs now, exists nowhere else\n' "$UNCOMMITTED"
printf '  undeployed   %-4s  commits — %s\n' "$UNDEPLOYED" "$DEPLOY_NOTE"
printf '  unpushed     %-4s  commits — vs %s\n' "$UNPUSHED" "$UPSTREAM"

if [ "$UNCOMMITTED" != "0" ]; then
  printf '\n  ── uncommitted ──\n'
  git status --porcelain | sed 's/^/    /'
fi

if [ "$UNPUSHED" != "0" ] && [ "$UNPUSHED" != "?" ]; then
  printf '\n  ── committed here only ──\n'
  git log --oneline "@{upstream}..HEAD" | sed 's/^/    /'
fi

# A number is not a verdict.  Say what to DO.
printf '\n'
if [ "$UNCOMMITTED" != "0" ]; then
  printf '  → commit with EXPLICIT paths (git add -- <files>).  A shared\n'
  printf '    index has swept another author'"'"'s in-flight files into the\n'
  printf '    wrong commit three times, twice breaking main.\n'
fi
[ "$UNPUSHED" != "0" ] && [ "$UNPUSHED" != "?" ] && \
  printf '  → git push %s %s  — this work is on one disk.\n' \
    "${UPSTREAM%%/*}" "$BRANCH"
[ "$UNCOMMITTED" = "0" ] && [ "$UNPUSHED" = "0" ] && \
  printf '  → clean: everything committed and pushed.\n'
printf '\n'

if [ "$CHECK" = "1" ]; then
  [ "$UNCOMMITTED" != "0" ] && exit 1
  [ "$UNPUSHED" != "0" ] && [ "$UNPUSHED" != "?" ] && exit 1
  exit 0
fi
exit 0
