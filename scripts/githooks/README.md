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
| 3 | No secret-shaped files, and no service-account key under ANY name | `.env`, `*.pem`, `*.key`, `id_rsa`; a Google key is caught by its content — except `.env.example`, also judged by content (below) |
| 4 | Warn when nothing is left unstaged | The footprint of `git add -A` in a shared tree |
| 5 | No import of a module the commit removes | A rename shipped with ten importers left behind; HEAD could not start for hours |

Rules 1–3 and 5 **block**. Rule 4 **warns** — it cannot know whose files
are whose, only that staging everything is the shape that went wrong.

## Rule 5, and why it is two checks

pyflakes reads ONE FILE at a time, so `from adapters.storage.object_store
import x` is invisible to it even while that module is being deleted in
the same commit. Each file is individually valid; the tree as a whole
will not import. Rule 5 resolves first-party imports against the tree
**as it will be** — HEAD's files, minus this commit's deletions, plus
its additions.

It checks both directions, because the obvious half misses the incident:

- **5a** a STAGED file importing a module that will not exist
- **5b** a DELETED module that surviving files still import — and those
  importers are usually *not* in the commit, which is exactly why
  checking only what you staged let the real one through

A RENAME counts as a deletion of its source path. Moving a package and
its own tests together — the ordinary way a feature moves house — used
to read as "you deleted a module these files still import", naming the
very files that moved with it. The exemption loosens nothing: 5a still
reads every staged file at its NEW path and refuses an import that will
not resolve.

Module paths only, never the imported names: `from a.b import c` is
satisfied by `a/b` existing, because `c` may be a name rather than a
submodule. Imports guarded by `try/except ImportError` are exempt —
there are 19 legitimate ones in this repo.

## Rule 3 catches a Google key by what it IS, not what it is called

The two service-account keys in this tree stay out of `git status` only
through the blanket `*.json` in `.gitignore` — which carries a dozen
`!` exceptions for source. An exception written later for a directory
(`!some/dir/*.json`) would silently un-ignore a key dropped there, and
nothing would object. So the hook reads the staged CONTENT: it PARSES
the file and refuses a JSON object whose type field says it is a
service account and which carries a private key — whatever the file is
called, which is also what catches a key renamed to look ordinary.

It parses rather than searching for those field names as text. The
first version grepped, and the first thing it refused was this README,
which has to name the fields to explain the rule. A guard that cries at
prose is a guard somebody switches off.

`.gitignore` keeps a second set of credential patterns at the very
bottom of the file, below every negation, because its LAST matching
rule wins. That is the belt; this rule is the braces.

## Rule 3 and the one `.env.*` that is tracked

`.env.example` is the template every deployment copies, so it is in the
repo and it is meant to be edited — every new setting gets documented
there. The name rule refused it, and the ways around a refused commit
are `--no-verify`, which switches off the other three rules as well, or
leaving the setting undocumented. An undocumented setting is how a real
key ends up pasted somewhere nobody is guarding, so the refusal was
working against its own purpose.

It now passes on CONTENT. A value is refused only when its key looks
secret-shaped (`TOKEN`, `SECRET`, `PASSWORD`, `CREDENTIAL`, `PRIVATE`,
`_KEY`) **and** the value does not read as a stand-in: placeholders
(`your_…`, `changeme`, `example`, `<…>`, `xxx`, `…_here`), URLs, bare
numbers and anything under 16 characters all pass, because those are
what a template is for. The offending key is named in the refusal.

Every other `.env.*` is refused on its name, as before.

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
