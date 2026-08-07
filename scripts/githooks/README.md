# Commit rules — enforced, not remembered

    git config core.hooksPath scripts/githooks     # once per clone

`core.hooksPath` lives in `.git/config`, which is not version-controlled,
so the hook ships in the repo but the wiring is a one-time local step.
Run `./scripts/where.sh` if you want to know whether it is on.

## The rules, and the incident behind each

| # | Rule | Why |
|---|---|---|
| 1 | Staged Python must parse | Production imports from this working tree, so a half-saved file is a half-deployed file |
| 2 | No undefined names in staged Python | A `NameError` killed every group-routed alert for four days; an unimported annotation sat red in CI for a week |
| 3 | No secret-shaped files | `.env`, `*.pem`, `*.key`, `id_rsa` |
| 4 | Warn when nothing is left unstaged | The footprint of `git add -A` in a shared tree |

Rules 1–3 **block**. Rule 4 **warns** — it cannot know whose files are
whose, only that staging everything is the shape that went wrong.

## Rule 4, the expensive one

A shared index swept another author's in-flight files into the wrong
commit three times in one week, twice breaking `main`:

- a commit took the **caller** and left the **callee** behind, so every
  public application submission raised `AttributeError`
- a commit took a mid-edit React component, and the dashboard rendered
  a black screen from a chunk referencing an unbundled symbol

Neither author did anything careless beyond `git add -A` in a tree
somebody else was also editing.

**Stage explicit paths. Always.**

    git add -- features/x/router.py tests/test_x.py

`git commit -- <path>` does not work for a *new* file (unknown
pathspec). The equivalent that does:

    git diff --cached --name-only        # must be empty first
    git add -- <your files>
    git diff --cached --name-only        # confirm the list
    git commit

## Checked against the STAGED content, never the working tree

Deliberate. A guard that stashes or checks out to inspect a commit can
destroy the work it is protecting — a `git stash pop` in this repo once
produced 35 conflicts and resurrected 27 deleted files. The hook reads
blobs out of the index with `git show :path` and touches nothing.

## Bypass

    git commit --no-verify

For when you have a reason. If you are reaching for it because rule 2
is wrong about your file, say so — the rule is meant to be tightenable,
not routine to skip.
