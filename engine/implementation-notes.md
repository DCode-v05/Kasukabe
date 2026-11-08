# Engine-managed filesystem — implementation notes

Running log for the `SEPTEMBER_HOME` work. Deviations from the agreed plan are
recorded under **Deviations** with the conservative choice taken.

---

## A. DSPy removal — DONE

**Intent:** remove DSPy entirely; the Learning Centre owns learning now.

**What was found.** `import dspy` appeared *nowhere* in the codebase. All 25
references were SQL column names and docstrings. The `dspy~=2.6` dependency was
pulling a large transitive tree (including `diskcache`) for zero imported code.

**Changes**

| File | Change |
|---|---|
| `pyproject.toml` | dropped `dspy~=2.6` |
| `Dockerfile` | dropped `/home/engineuser/.dspy_cache` from the `mkdir` |
| `docker-compose.yml` | dropped the `.dspy_cache` tmpfs mount |
| `migrations/002_utility_directory.sql` | `user_agents.dspy_optimized_state` → `learned_state`; `user_skills.optimized_state` → `learned_state`; `user_agents.dspy_module` dropped |
| `src/utility_directory/ud_write.py` | column rename; `dspy_module` removed from INSERT / VALUES / ON CONFLICT / params tuple; `update_agent_state(dspy_state=)` → `learned_state`; docstrings |
| `src/utility_directory/ud_full.py` | column rename; local `dspy` → `learned`; module docstring |
| `src/utility_directory/platform_registry.py` | column rename in both JSON-parse tuples |
| `src/engine_core/schema.py` | docstring |
| `scripts/ci.sh` | comment; 3 stale CVE ignores removed |

Migration edited **in place** rather than appended as an ALTER — there are no
live systems, per the standing rule for this repo.

**Tests** — `tests/test_ud_learned_state.py`, 20 cases, no mocks. Real migrated
SQLite via the shared `db` fixture, exercised through the live `UDWrite` /
`UDFull` / `PlatformRegistry` call sites rather than raw SQL, so a rename that
misses a call site fails here. Covers: schema shape, absence of the old columns,
JSON round-trip, the `COALESCE` preserve-on-upsert guard, first-use `None`,
`update_agent_state` hit and miss, dependency absence, and a repo-wide scan
asserting zero `dspy` identifiers remain in `src/` and `migrations/`.

Written first; **19 of 20 failed before the change**, all 20 pass after.

---

## A2. Dependency slimming

Follow-on to the DSPy removal: audit every declared dependency for real use.

**gcc removed from the runtime image.** The `base` stage installed it alongside
bubblewrap. Verified unnecessary three ways: `pip install --only-binary=:all:`
resolves the entire tree to prebuilt wheels; nothing in `src/` references a
compiler; the sandbox allowlist never exposes one. It cost **166 MB**. Comment
left in the Dockerfile so a future source-built dependency goes to a separate
build stage rather than back into the runtime image.

**`prompt_toolkit` removed.** Zero imports anywhere in `src/`, `tests/`, or
`tui.py`. Declared, installed, never used.

**`rich` + `textual` moved to a `[tui]` extra.** Only `tui.py` imports them
(reached via `python -m src`); the uvicorn server path never does, so the
runtime image no longer carries them. `dev` depends on `september-engine[tui]`
so tests that import `tui` keep working.

**Results**

| Measure | Before | After |
|---|---|---|
| Full suite | 5307 passed | **5307 passed, 18 skipped, 0 failed** |
| Runtime image | 585 MB | **327 MB** |
| Test image | 1.42 GB | **464 MB** |
| `import src.server` (runtime image) | — | **265 ms** |
| `pip-audit` | exit 1, 8 suppressions | **clean, 0 suppressions** |

Timings are QEMU x86_64 on Apple Silicon (≈5× penalty), so native is
substantially faster. The image and import figures both feed the Space
cold-start question — see `Vat/sept-spaces/space-decision.html` §10.

---

## B. Skills — roots as data, SKILL.md, metadata budget

**Roots are values, not call sites.** `SkillRoot(path, source)` plus
`load_skills_from_roots(roots)` replace the two hardcoded `load_skill_folders`
calls. Precedence is list order — reverse the list, reverse the winner — so
adding a managed or registry-fetched source is configuration rather than an edit
to `catalog.py`. `default_skill_roots()` composes the normal boot list and takes
`extra_roots`. Modelled on codex `core-skills/src/loader.rs:195`.

`SkillRoot.path` resolves to an absolute path on construction, so a relative
root can never quietly resolve against the process cwd — codex's
`AbsolutePathBuf` discipline, in the form Python allows.

**Domain packs removed** from both docstrings and the loader. They were listed
as source #2 of three and never loaded; a test now asserts the word is gone
from `catalog.py` so it cannot come back as documentation-only.

**SKILL.md is the format.** YAML frontmatter (`name`, `description`, optional
`short-description`, accepted flat or nested under `metadata:` as codex writes
it) with the body as instructions. Legacy `skill.json` still loads, SKILL.md
wins when both are present, and every write emits SKILL.md — so a legacy folder
migrates the first time it is edited, and `update_user_skill` then removes the
`skill.json`/`prompt.md` pair rather than leaving two sources that can disagree.
`scripts/`, `references/` and `gotchas.md` are unchanged.

**`has_*` flags are now derived from disk**, not persisted. They used to be
written into `skill.json` at create time and could drift from what the folder
actually contained. Stored metadata that restates the filesystem is metadata
that can lie.

**Failure isolation matches codex:** a missing root is logged at INFO (a
configured source that never loads must leave a trace), and one unreadable skill
is logged at WARNING and skipped without taking its root down.

**Metadata budget.** `skill_metadata_budget(context_window)` returns 2% of the
window, or an 8,000-character fallback when the window is unknown — codex's
constants from `core-skills/src/render.rs:18`. `apply_skill_metadata_budget`
returns `(kept, dropped)` and logs what it dropped, because silent truncation
reads downstream as "the model saw everything". It always keeps at least one
skill when any exist.

**Wired to a live call site:** `Search.skills()` applies the budget on every
read, and `bootstrap.py` sizes it from the agent model's real context window via
`herald.catalog.get_context_window(settings.llm_model)`. A small-context model
is no longer handed the same skill list as a large one.

**Tests** — `tests/test_skill_roots.py`, 27 cases, real files on real disk.
Written first, red before the change. Three pre-existing tests asserted the old
on-disk format (`skill.json` / `prompt.md`) and were updated to assert the same
*behaviour* through the new format.

### B-8 · Filesystem is the source of truth for skill definitions

**A real bug, not just tidying.** A user-created skill was written to disk *and*
given a `user_skills` row. Every reader that merged the two stores emitted it
**twice** — once properly from the catalog, and once as a bare name whose
`description` column the live write path never populates. The model saw a
duplicate with an empty description, in both `Search.skills()` and
`UDMeta.get_skills_meta()`. A test reproduced it before the fix.

The write side was already filesystem-first: `server.py` and `thread.py` call
`create_user_skill` and then insert a row of `(id, name, source, status)`, and
`UDWrite.write_user_skill` — which wrote the definition columns — had **no
production callers at all**. Only the reads were wrong.

| Change | Effect |
|---|---|
| `user_skills` drops `source_code`, `prompt_template`, `config`, `capabilities`, `input_signature`, `output_signature`, `description` | one definition, on disk |
| `Search.skills()` reads the catalog only | duplicate gone; real description survives |
| `UDMeta.get_skills_meta()` reads the catalog only | same |
| `UDFull.get_skill()` merges only `learned_state` | no competing definition |
| `UDFull.get_skill_user_created()` reads the catalog | a state row can no longer conjure a skill with no folder |
| `UDWrite.write_user_skill()` narrowed to state columns | definition keys are ignored, not persisted |

Both readers now stamp the real provenance from `CatalogEntry.source` instead of
hardcoding `"platform"`, so a user or managed skill reports what it actually is.

**Fails loudly.** `load_skills_from_roots` now raises `NotADirectoryError` when a
root exists but is not a directory. An absent root stays tolerated and logged —
a user may simply have no skills yet — but a path that points at a *file* is a
misconfiguration, and treating the two alike turns a typo into a silently empty
skill list rather than a startup failure.

**Tests** — `tests/test_skill_source_of_truth.py`, 14 cases: schema shape, the
duplication closed in both readers, a stale state row inventing nothing,
`UDWrite` refusing definitions, `UDFull` reading the folder, and the loud
failure. Real files, real migrated SQLite, live call sites, no mocks.

Full suite: **5348 passed, 18 skipped, 0 failed.**

### Definition of done — Phase B

| Capability | Live call site | Real end-to-end test | Fails loudly |
|---|---|---|---|
| Roots as data | `load_catalog` → `default_skill_roots` | `test_skill_roots.py` (7) | `NotADirectoryError` on a non-directory root |
| SKILL.md format | `create_user_skill` / `update_user_skill` / loader | `test_skill_roots.py` (11) + 3 updated legacy tests | malformed frontmatter logged + skipped, not half-served |
| Metadata budget | `Search.skills()`, sized in `bootstrap.py` from `get_context_window` | `test_skill_roots.py` (8) | drops logged with counts, never silent |
| Filesystem as truth | `Search.skills()`, `UDMeta`, `UDFull`, `UDWrite` | `test_skill_source_of_truth.py` (14) | definition columns absent — a write attempt raises |

---

## C. SEPTEMBER_HOME — one root for everything the engine owns

`src/infra/september_home.py` resolves the root and names the layout. Resolution
follows codex's `find_codex_home` (`codex-rs/utils/home-dir/src/lib.rs`):

| Case | Behaviour |
|---|---|
| `SEPTEMBER_HOME` set, is a directory | use it, resolved absolute |
| set, missing | **FileNotFoundError**, naming the path |
| set, not a directory | **NotADirectoryError**, naming the path |
| unset | `~/.september`, created |

The asymmetry is the point: an unset root is a fresh install and should work; a
root that was set and does not resolve is a bad mount or a typo, and booting
anyway writes a user's memory somewhere nobody will look.

**Layout.** `brain.sqlite` at the root, plus `config/ credentials/ skills/
plugins/ workspaces/ sidechains/ results/ cache/ tmp/ log/`.

**Moved onto it:** the database and permissions file (via `Settings.__post_init__`,
which fills only empty fields so an explicit value still wins); the engine key
hash (`auth.PERSISTED_KEY_HASH_PATH` constant → `persisted_key_hash_path()`);
sidechains (`/tmp/engine-sidechains` → `<home>/sidechains`); thread tool-result
overflow (`mkdtemp` in TMPDIR → `<home>/results`); sandbox workspaces
(`tempfile.gettempdir()` → `<home>/workspaces`).

**`permission_store_project_path` deleted.** The cwd-relative `.engine/permissions.json`
tier is gone. It was dead in production only because the read-only rootfs made
the file uncreatable — a mitigation that disappears when the engine stops being
deployed as a container.

### Ported from the references

**Directory mode 0700** — Claude Code chmods every directory holding session
data (`utils/sessionStorage.ts:640,1601,1655,2581`). Our root holds a user's
memory, credentials at rest, and the engine key hash; at the default 0755 any
local user on a shared host could read all of it. Applied to the root, every
subdirectory, and each write site.

**Create at the write site, not only at boot.** Neither reference pre-creates
its layout: codex calls `create_dir_all` at the point of use
(`core-plugins/src/startup_sync.rs:193`), Claude Code does `mkdir(recursive)`
per write. `ensure_layout()` still runs at boot so a captured tree has a stable
shape across engines, but nothing depends on it having run.

**Temp inside the root.** codex keeps `.tmp` under `codex_home`. Ours is
`<home>/tmp`, which also means one mount to grant exec rather than relying on
`/tmp` semantics.

### Bugs found and fixed on the way

**`orphan_detector.py` matched a literal that was already wrong.** It tested for
`"/tmp/workspace-"` while workspaces came from `tempfile.gettempdir()`. Setting
`TMPDIR` therefore *already* disabled orphan reaping silently, letting killed
grandchildren accumulate as zombies until the container ran out of PIDs — the
exact failure `docker-compose.yml` documents at length. It now resolves the
workspace root from the executor, with both sides pointing at one function.

**`SqlitePool.connect()` leaked a connection on partial failure.** aiosqlite
starts a worker thread inside `connect()`; an exception from a PRAGMA left that
thread alive with no handle to close it, and it later raised "Event loop is
closed" from whatever unrelated code was running when it was collected. Setup
after the connection opens is now wrapped, closing before re-raising.

**`tests/channel_harness.make_db()` never closed its pool** — 40 call sites, one
leaked connection each. Pools are now registered and closed by an autouse
fixture, so the leak is structurally impossible rather than a thing to remember.

**`shutdown()` never closed its own database.** It delegated entirely to a
*process-global* cleanup registry and then called `clear_registry()`. A nested
shutdown therefore emptied the registry before the outer engine's own entry ran,
so `shutdown(state)` closed nothing. `shutdown` is now self-sufficient for the
state it was handed: it closes `state.db` directly after the registry runs
(`close()` is idempotent, so the normal path is unchanged).

Finding it needed instrumentation — the warning surfaced in a different
unrelated test on every run, because a thread exception appears wherever the
garbage collector happens to be. A temporary conftest hook wrapping
`SqlitePool.connect`/`close` recorded which test opened a pool that never
closed, and named it in one run.

### Permission provenance

`PermissionRuleSource` → `PermissionProvenance`, using the same four words as
the asset system (`asset_directory/states.py`), so authority has one vocabulary
across the engine rather than one per subsystem:

| Was | Now |
|---|---|
| `SYSTEM_ALLOW` | `SEPTEMBERAI` |
| `AGENT` | `ENGINE` |
| `OPERATOR_GLOBAL` | `USER` |
| — | `MANAGED` (reserved for the control plane) |
| `OPERATOR_PROJECT` | deleted |
| `SYSTEM_DENY` | `HardDeny`, **outside** the enum |

`HardDeny` is deliberately not a member. Catastrophic denials are an invariant
above the ladder, not a tier on it — folding them in would necessarily place
them at the bottom (they are code-defined), letting a user rule outrank
`rm -rf /`. That would have read as a clean rename in review.

`PERSISTABLE_PROVENANCE` is `{USER}` alone. `EDITABLE_PROVENANCE` is
`{ENGINE, USER}` — editability is a separate axis from authority, the
distinction Claude Code draws with `EDITABLE_SOURCES`, which excludes its
managed tier for the same reason.

Legacy source strings still load: `operator_project` folds into `USER` rather
than vanishing, so a rule someone already wrote keeps applying at the tier that
replaced it. `format_source` now normalizes before display, so the list of old
names lives in one place instead of being mirrored into `SOURCE_DISPLAY` where
the two could drift.

### Restore proof

`tests/test_home_restore.py` is the claim the whole phase exists to make: write
real state through live call sites into one root, delete the engine, point a new
engine at a *copy* of that root, and read it all back — conversation, skill,
permission grant, key hash. Plus a check that no derived path escapes the root,
and that a root pointing at a file stops `Settings` construction rather than
failing at first write.

Full suite: **5402 passed, 18 skipped, 0 failed, 0 warnings.**

---

## Deviations

**A-1 · `user_agents.dspy_module` dropped rather than renamed.**
The plan said rename the state columns; it did not mention `dspy_module`. That
column is written by `write_user_agent` and **read by nothing** — no reader
anywhere in `src/` or `tests/`. It stored a DSPy module class name, a concept
with no meaning once DSPy is gone, so renaming it would have preserved a field
that is both dead and misleading. Dropped. No live systems, so no data loss.
Reinstating it later is a one-line migration edit.

**A-3 · `septemberai` removed, then restored — record of a real miss.**
An import audit for `septemberai` returned zero hits, so it was dropped as
unused. The build then failed collection on two test modules: the distribution
**`septemberai` installs as the module `september`**, and it is imported by
`herald/catalog_sync.py`, `memory/embeddings.py`, and `server.py`. Restored,
with a comment on the dependency line recording the name mismatch so the same
search does not mislead the next person. Lesson: audit distributions by their
*import* name, not their package name.

**A-2 · `user_skills.optimized_state` renamed to `learned_state` for symmetry.**
It carried no `dspy_` prefix, so a literal reading of "remove dspy" would have
left it alone. Leaving it would have meant the two sibling tables naming the
same concept differently. Renamed to match.

---

## A3. Security bumps — audit gate now clean

The `pip-audit` gate in `scripts/ci.sh` was failing (exit 1) *before* this work:
`pillow` 11.3.0 carried ~20 advisories and `cryptography` 43.0.3 carried three
more, well past what the ignore list covered. Removing a package cannot
introduce advisories in others, so it predated the DSPy change. Bumped on the
user's instruction rather than suppressed.

| Package | Was | Now | Why that floor |
|---|---|---|---|
| `cryptography` | `~=43.0` (43.0.3) | `>=48.0.1,<49` | 48.0.1 is the first release clearing the backlog |
| `Pillow` | `~=11.0` (11.3.0) | `>=12.3.0,<13` | 12.3.0 is the first release clearing the backlog |

Both are major bumps, but the API surface in use is small and long-stable:
Fernet plus `x509` / `ec` / `hashes` / `serialization` for cryptography;
`Image.open` / `resize` / `convert` / `save` / `Image.Resampling` for Pillow.

**Result: `pip-audit` reports "No known vulnerabilities found" with NO ignores at
all**, so `PIP_AUDIT_IGNORES` is now empty. Full suite green on the new versions.

One follow-on that was required, not optional: emptying that array broke the
audit target under `set -u`, because **bash 3.2 — still the macOS system bash —
treats `"${empty[@]}"` as an unbound variable**. Both expansions in `t_audit`
now use the `${arr[@]+"${arr[@]}"}` guard, verified against bash 3.2.

---

## Carried forward — for the registry work

Codex downloads remote skills as a **validated zip** into
`codex_home/skills/<skill_id>` (`core-skills/src/remote.rs:173`): it checks the
payload really is a zip before extraction, creates the directory under the
managed home, and extracts with an expected path prefix so an archive cannot
write outside its own skill directory.

That is the shape the September registry work needs — remote skills landing
inside the managed root, under the same precedence rules as local ones, with
extraction bounded to the skill's own directory.
