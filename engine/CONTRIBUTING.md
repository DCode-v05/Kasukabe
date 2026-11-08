# Contributing

## Pre-commit hooks

This repo uses [pre-commit](https://pre-commit.com/) to run lint, format,
hygiene, and house-style checks on the files you stage — keeping your commits
clean without touching the rest of the codebase. Set it up once:

```sh
pip install pre-commit && pre-commit install
```

After that, the hooks run automatically on every `git commit`. Most of them
operate on **staged files only**, so the existing repo-wide lint debt on files
you didn't touch won't block your commit. The exception is the style gate
(below): it diffs the working tree against `HEAD` rather than the index, so it
also sees unstaged edits and untracked `*.py` files under `src/` and `tests/`.
Because it still judges only the lines your change adds, already-committed code
never blocks you either way.

The configured hooks (see `.pre-commit-config.yaml`):

- **ruff** (with `--fix`) — lints and auto-fixes your staged Python files.
- **ruff-format** — formats your staged Python files.
- **trailing-whitespace**, **end-of-file-fixer**, **check-yaml**,
  **check-merge-conflict** — standard hygiene checks.
- **style-gate** (local) — runs `scripts/check_style_diff.py`, the same
  house-style gate `scripts/ci.sh style` runs. Pre-commit judges your staged
  change against `HEAD`; `CI_STYLE_BASE=origin/main scripts/ci.sh style` judges a
  whole branch. On the lines your change *adds* to `*.py` files under `src/` and
  `tests/`, it rejects em dashes (U+2014) and newly defined leading-underscore
  identifiers (dunders and a small allowlist in `check_style_diff.py` are
  exempt; only definitions and bindings are judged, so reading an existing
  private name never trips it). Append `# noqa: style` to a line to suppress a
  false positive.

To update the pinned hook versions to their latest releases:

```sh
pre-commit autoupdate
```

To run the hooks manually against everything you've staged:

```sh
pre-commit run
```

To run them across the whole repo (note: this will surface the pre-existing
lint debt):

```sh
pre-commit run --all-files
```

## Running the tests

The suite runs inside the privileged `test` container so the bubblewrap sandbox
can create namespaces and `fork()` — matching production:

```sh
docker compose run --rm test
```
