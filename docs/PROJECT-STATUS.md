# Where this project stands — 26 September 2026

*Written against branch `evals/proving-the-town` at `0b27d46`, pushed and in
sync. Every figure here was read from the tree or the trace, not recalled.*

---

## 1. The short version

An independent evidence instrument (`app/proof/`) now exists, runs in about
40 seconds, and gates the repository. It judges **what the town actually
did**, never what the source says, and it answers `proven` / `unproven` /
`failed` — where `unproven` is never scored as a pass.

```
239 claims judged: 185 proven, 54 unproven, 0 failed
OK: nothing regressed, nothing newly failed.
```

A 30-resident election run completed on that branch and closed seven rows
that had never been closed before.

---

## 2. The instrument

`cd app && python -m proof`

| area | asks | time |
|---|---|---|
| `reach` | has this verb ever been used, in any recorded run? | 19.6s |
| `agility` | what does the **frozen parser** make of another model's stream? | 9.4s |
| `stories` | what does the **real town** do when a verb is called? | 10.7s |
| `replay` | would the calls that once worked still be accepted? | 0.8s |

The governing rule is **nothing is green that never ran**. No eval asserts
on source text or `inspect.getsource`, and none runs against a stubbed
ledger — only the model is ever faked, at the `BedrockGateway` Protocol.

The gate is a **ratchet, not a target**: nothing that was proven may stop
being proven, and nothing may newly fail. Baselined failures hold the build
green, because a gate that is red on day one teaches a team to bypass it.

`.github/workflows/proof.yml` is the repository's first CI workflow. It has
never executed on GitHub — valid YAML, unproven in practice.

---

## 3. The run: `election30`

30 residents, an election from the first tick, two full game-days.

| | |
|---|---|
| spend | **$95.89** of a $99.75 cap |
| ticks | 36 → **324** — the full scenario, *not* stopped by budget |
| model calls | 13,360, **18 failed (0.13%)** |
| residents | **30 alive, 0 dormant, 0 dead**, start to finish |
| turns recorded | 2,616 across 30 of 30 session files |
| utterances | 6,329 · transactions 21,613 · events 57,841 · skills 711 |
| self-score | **14 of 42** run-decidable checklist items held |

Politics: **2 elections tallied, 22 ballots, 7 candidacies, 1 regime change,
1 sitting convened.**

### Seven previously-unreached verbs, now called

`convene` · `do_in_order` · `file_complaint` · `pay_tax` · `pray` ·
`update_goal` · `work_site`

`do_in_order` is the notable one — zero calls across runs 25 through 28b
against 5,544 resident calls, the finding that prompted the whole authoring
investigation.

### Artifacts

The full run report is `docs/runs/series/election30/report.md` (10.4 MB, 74,463 lines), with 30 per-turn files under `docs/runs/series/election30/sessions/`.

---

## 4. How to resume this run

The material is there: checkpoints at ticks **144, 288 and 324**, plus
`engine-homes.tar.gz` (634 MB) in `app/data/checkpoints-election30/`.

```bash
cd app
RESUME=1 bash run-scenario.sh world/scenarios/election_at_30.json election30 4
```

**The budget does not reset, and that is deliberate.**
`_governor_seeded_from` reads prior spend out of the trace database and
seeds the governor with it — INFRA-14, *"the cap is the only thing between a
bug and an unbounded Bedrock bill, and it was reset by a `docker compose
restart`."*

So a second $100 means raising the ceiling to **cumulative** $200, not
resetting it to $100:

```
SPEND_CAP_USD=205.00          # in app/.env — edit in BINARY, see §7
--budget-cap-cents 20000      # or KASUKABE_BUDGET_CAP_CENTS
```

The runner self-adjusts to stay clear of the broker: with the broker at
$105 it ran to $99.75, *"525 below the broker's cap"*, leaving room for the
end-of-run learning drain. Keep that gap.

**Resuming on a different brain is not yet a flag.** The model is pinned in
three places — `engine/src/sim/model_profile.py`, and `broker/config.py`'s
`MODEL_ALLOWLIST` and its price table — and only Kimi, DeepSeek and Qwen are
allowlisted. There is no GPT lane. Making this env-selectable is the largest
outstanding item (§6).

---

## 5. What was fixed on this branch

32 commits. Grouped by what they were:

**Model agility — 5 found, 5 fixed.** Refusals arriving as ordinary turns;
two interleaved tool calls merging into one; a lost call reported as
`TOOL_USE`; unparsed markup billed for nothing. Three needed the frozen
engine and are recorded in `docs/decisions/LAW-1-MANIFEST.md`, each its own
`engine(law1):` commit with a revert.

**`search_memory` was denied on every call it ever received** — registered,
never granted a permission rule, so all 29 calls were refused before the
handler. It hid because a denial fires no result event, so the row carries
no status at all. Third time this gap has bitten; now a test blocks a
fourth.

**Recording.** `run-election.sh` discarded every run's session logs — the
reason `teambox50` is permanently unauditable. Launchers were not
executable (`Permission denied` on Linux) and had no `.gitattributes`, so
`.sh` files could arrive CRLF and kill bash.

**Order-dependent tests.** "The engine suite is green" depended on which
subset you ran. A sim boot leaked `DISABLE_AUTO_COMPACT=1` into `os.environ`
process-wide; a test borrowed a global another test happened to set;
`test_integration_boot.py` hung with **zero output** for three separate
reasons. Found on the way: `bootstrap.shutdown()` never called
`catalog_sync.shutdown()`, so every engine left a resync task polling.

**Windows portability** — launcher Python resolution, the recorder's Chrome
lookup, and `loop.add_signal_handler`, which raises `NotImplementedError`
there.

---

## 6. What is open

| | |
|---|---|
| **Env-selectable brain** | Chosen, not built. Needed before any resume on a different model. The largest outstanding item. |
| **Resume never verified end to end** | Checkpoints and homes exist; the resume path itself has not been exercised. |
| `tool_invocations` ring cap | Froze at exactly 5,000 rows at tick 111 while the world ran to 324. Verb evidence survived only because the session logs carry it. |
| malformed `toolUse.input` in history | Behind some of the 18 failures: a bad call persists in the transcript and Bedrock rejects later requests that replay it. |
| 54 unproven rows | Almost all `verb-reach`. These close by **running**, not by coding. |
| `what a teacher keeps` | Not a defect — the cost is disclosed and pricing above 15 sim profits. Whether 15-against-15 is the right calibration is a design decision. |
| 5 engine test failures | Pre-existing, verified identical at the branch point: Windows path-traversal and three `TestNetworkPermission` cases needing a real sandbox. |

---

## 7. Operational facts worth not rediscovering

- **Residents pay for their own thinking from a USD wallet.** `$6.00` per
  agent is the working level — `genesis_20.json` and `genesis_50_town.json`
  both use it, and both ran successfully. `genesis_30.json` ships at `$2.00`
  and would have stalled this run at about $60 with everyone dormant;
  `genesis_30_town.json` was created at $6.00 for that reason.
- **`app/.env` must be edited in binary.** A text-mode write rewrites all
  248 line endings; a stray `\r` inside the AWS secret reads as a bad
  credential. A backup sits at `.env.bak-before-cap-raise`.
- **Never `git add .` here.** `.secret` and `.optoken` are not gitignored
  and have never been committed. Stage explicit paths.
- **The AWS key is alive but was never rotated.** Preflight confirms
  `account 771650096076 (intern-pool)`, 3 lanes, 300 RPM. It was leaked on
  2026-09-21; `docs/runs/series/*/KEY-NOT-ROTATED.txt` records two runs
  started on it knowingly. The launcher gate that used to ask about it was
  removed — it never checked anything and was twice answered untruthfully.
- **`python -m broker.preflight`** is free: STS `get_caller_identity` needs
  no Bedrock permission and separates "no credentials" from "credentials
  Bedrock will not accept". Run it before any paid run.
- One tick = **10 minutes** of in-world time; 144 ticks = one game-day. Wall
  clock ran about 35s per tick with 30 residents.

---

## 8. Cost recorded today

| | |
|---|---|
| `election30` | **$95.89** |
| an accidental run I started | **$0.51** — `--dry-run` is not a flag on `run-scenario.sh`; it became the run *name* and launched a real run. Killed after 112 calls; artifacts kept at `data/accidental-2026-09-26.db`. |
| **total** | **$96.40** |
