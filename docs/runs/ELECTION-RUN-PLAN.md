# The election run: the plan

*Prepared 2026-09-22. Not started. It waits for a rotated AWS key and the
operator's go-ahead. `app/run-election.sh` refuses to start without
`CONFIRM_KEY_ROTATED=1`.*

## What it is for

This run is planned so that as many of the checklist's pending items as possible are
**checked in this one round**. Its report opens with every item a run can decide,
scored from the trace. In it the town:

- holds its first election from start to finish;
- has a sitting government for a full day;
- crosses a day boundary under that government;
- lives through a second and third game-day.

No run has got past tick 194. The first count is at tick 432.

## Two sizes

| | 20 residents | 50 residents |
|---|---|---|
| Command | `run-election.sh 20` (`run20.sh` still works) | `run-election.sh 50` |
| Where | a laptop | **the team box** (`docs/TEAM_BOX_GUIDE.md`) |
| Cast | `genesis_20_funded.json` | `genesis_50_funded.json`, the cast of the 50-resident run of 2026-09-16, so the two runs can be compared |
| May stand and vote | 16 of 20 | 34 of 50 |
| Thinking money | $14 each, treasury $720 | $14 each, treasury $300 |
| Expected cost | about $175–220 | about $440–550 |
| Spend cap | $280 (broker $290) | $650 (broker $660) |
| Wall-clock | about 7 h (3½–8½); about 5½ h to tick 480 | the same, about 7 h (3½–9), over two of the box's 4-hour sessions |
| Memory for engines | 20 × 64 MB = 1.3 GB | 50 × 64 MB = 3.2 GB |

**Why it takes hours, not one or two.** An earlier version of this plan said
1–2 hours, from the request quota alone. That was wrong. The runner moves the
clock only when every due resident's turn has finished, so a tick lasts as long
as its slowest turn. Run 29 averaged 56 s a tick (41–51 s when replayed with
today's 6-call limit on a turn). The 50-resident run averaged 22.5 s, with most
of its town out of thinking money. Plan at about 45 s a tick: 554 ticks to tick
590 is about 7 hours, and 444 to tick 480 is about 5½. Residents take turns every
third tick at night too, so nights are not faster. Neither size is quota-bound:
20 residents need about 70 requests a minute and 50 need about 160. Run the two
one after the other, because both draw on the same AWS account's quota.

**Where the money goes.** All of it is model calls. From run 29's calls by
purpose, and from the median call measured on 2026-09-17:

- re-reading the tool list, about 54%;
- the resident's persona and brief, 11%;
- its history, perception and earlier results in the turn, 22%;
- its replies, 4.5%;
- the note after each turn, 5.4%;
- memory upkeep, 2.5%;
- learning, 0.1%.

Every game-day costs about the same. For the 20-resident full round, that is
$34–43 for candidacy (from dawn), $45–58 each for the campaign, the vote and the
government's day, and $4–6 after the day boundary.

**Why 50 goes to the team box.** Real engines run one process each. The
"namespace" backend cannot host an engine (CORE-20). Preflight uses the
64 MB-per-engine figure measured on this Mac. The Mac has 8 GB, with 1.6 GB
free while an editor, a browser and WhatsApp are open, so preflight refuses 50
(3.2 GB). It would currently refuse 20 as well (1.3 GB). **Close the browser
and other apps before the 20-resident run**, and preflight will name the
shortfall if it is still short. The team box has 64 GB and 32 CPUs, and ran
the earlier 50-resident run.

## Who takes part

**Every resident aged 18 or over may stand and vote.** That is the charter's
rule: `suffrage_min_age = 18`, plus residency, which means being a resident. The
same rule covers standing and voting.
`world/tests/test_the_election_says_where_when_and_who.py` and
`test_the_election_run_comes_in_two_sizes.py` count every eligible resident in
both casts. Nobody is told to take part. Each resident's perception states the
rules as facts:

> Candidates declare, and residents vote, in person at Kasukabe Government
> Complex; residents aged 18 and over may do both. Candidacy closes game-day 1,
> 00:00; voting opens game-day 2, 00:00; voting closes game-day 3, 00:00.

## The schedule

*Changed 2026-09-23: the election is scheduled from the tick the run starts on,
not from tick 0. A run begun at dawn used to sleep through 36 ticks of its own
candidacy window. Every row below moved on by six game-hours.*

| Phase | Ticks | Game time |
|---|---|---|
| Candidacy | 36–180 | from dawn on day 0 |
| Campaign | 180–324 | day 1 |
| Voting | 324–468 | day 2 |
| **Count, and seats filled** | 468 | game-day 3, 06:00 |
| The new government's day | 468–576 | day 3 |
| **Its first day boundary** | 576 | a changed tax rate is first charged, and government wages are first paid |
| **Stop** | 590 | game-day 4, 01:20 |

`STOP_TICK=480` is the cheaper run. It now ends two ticks after the count, and
leaves GOV-3, GOV-5, GOV-6, GOV-7, ECON-11 and X17 without the day they need;
`STOP_TICK=500` gives the result five game-hours on screen for about $5 more.

**The $15–20 alternative.** `docs/SCENARIOS.md` and
`world/scenarios/election_at_dawn_8.json`: eight residents, a government already
sitting, an election held in one game-day (counted at tick 108) and a stop past
the first day boundary, for $14.40–18.30 and about 1–1.5 hours. It answers the
election and sitting-government items; it cannot answer anything that needs a
second and third game-day, and it is not comparable with the 50-resident run.

## What the run checks: all 105 open items

**Scored by the run itself (30).** They open the report as a table, one query
each (`world/run_checklist.py`):

> ECON-5, ECON-7, ECON-8, ECON-9, ECON-10, ECON-11, ECON-12, ECON-14,
> GOV-1, GOV-2, GOV-3, GOV-5, GOV-6, GOV-7, GOV-8, GOV-9, GOV-10, G6, C-A,
> N6, N10, N15, N19, X2, X5, X14, X15, X16, X17, X19

The same table re-measures the gates (G1–G5, ES4, ET1, ET5) and the items
already ticked (ECON-2/3/4, N14, N16), so a regression shows.

**Answered from this run's data by a query afterwards (46).** The run records
everything these need. Most of them need a day 2 and a day 3, which this run
is the first to have:

> T2, ECON-6, ECON-13, SG5, C-B, C-E, C-F, Q1, Q2, Q7, Q9, Q10, Q11, Q13,
> Q14, Q15, Q16, Q17, Q23, Q24, Q25, Q26, Q27, N2, N4, N5, N7, N8, N9,
> N11, N12, N13, N17, N18, N20, N21, N22, N23, N24, X3, X4, X7, X8, X10,
> X11, X13

Q9, Q11 and N17 read the engine homes, which are now kept while the run is
going (SG7).

**Given their first chance, if residents do it (7).** The mechanisms exist and
the run gives the time. Whether a resident invents a tool others use, or
commits a crime that reaches a verdict, is theirs:

> T3, C-G, Q3, Q4, Q5, Q6, Q20

**Need more than one run, or more days (11).**

> T1 (a finding seen twice), C-H, C-I, Q18, Q19 (a transition needs several
> elections), Rung 1, Rung 2, Rung 3, and the three "done looks like" lines

**Need something no run provides (11).**

- A judge model: Q8, Q12, X6, X18, X20.
- A paid evaluation: X1, X9.
- A design not yet built: Q21, Q22.
- An inventory of the code: X12.
- Ongoing across all runs: Q28.

## What you watch, and the video

`run-election.sh` starts, all bound to the one world on 127.0.0.1:8100:

- the broker on :8200;
- the runner with the embedded world on :8100;
- the town viewer on :8400;
- the observatory on :8500;
- the recorder.

Watch at
`http://127.0.0.1:8400/township-v2.html?book=election&roofs=off&camera=government:2.2`.
On the team box, forward the port first:

```
ssh -L 8400:127.0.0.1:8400 team@ADDRESS
```

The viewer centres on the Government Complex, roofs off, so residents show as
people. It carries an election strip on the map and an Election book with the
rules, candidates and running totals, then the result, then the next election.

The recorder (`app/scripts/record_ui.py`) drives headless Chrome at 1920×1080
(DevTools on :9600, outside the engine port range) and takes a frame every
2 seconds. It stops when the world stops answering, and ffmpeg writes
`<OUT>/video/run.mp4`. The frames stay on disk; `--encode-only` rebuilds the
video. On the team box, if there is no Chrome or ffmpeg:

```
uv pip install --python /opt/venv/bin/python playwright imageio-ffmpeg
/opt/venv/bin/python -m playwright install chromium
```

The recorder finds both. No root is needed.

## The dress rehearsals (free)

`app/scripts/rehearse_ui.py [--genesis genesis_50_funded.json]` boots the
same town in mock mode, with no model calls, at one tick a second.
`--stage-election` casts candidacies and ballots through the same
`ElectionEngine` calls the residents' tools make. Rehearsal output is never
research data.

| Rehearsal | Candidates | Ballots | Result | Video |
|---|---|---|---|---|
| 20 residents, three runs | 4 | 13 | PM Tobias Holloway and 3 ministers | ✓ |
| 50 residents | 4 | 29 | PM Ainsley Holloway (10 votes) and 3 ministers | ✓ |

In all of them the viewer was bound to the live world (20 of 20, then 50 of
50), and the final frames show the result.

Defects the rehearsals found, all fixed:

- **The run stopped on the tick of the count**, so nobody saw the result. Runs
  now stop at a given tick (`--max-ticks`).
- **The result disappeared at the count.** The latest result is now carried
  separately and shown first.
- **The map strip ignored `hidden`**, and a stopped world blanked the panel.
- **Residents were invisible**, under roofs at whole-map zoom. The viewer now
  takes `roofs=off` and `camera=`.
- **The recorder's Chrome port (9333) sat inside the engine port range.**
- **The election notice lagged a tick**, and said "scheduled" at dawn.

## To start it

1. **Rotate the AWS key.** An account admin does this in the IAM console, then
   puts the new pair in `app/.env` (and in the team box's `app/.env` for 50).
2. **Run 20, on a laptop with the browser and other apps closed**, on power.
   `caffeinate -i` keeps the Mac awake for the 7 hours:

   ```
   cd app && CONFIRM_KEY_ROTATED=1 caffeinate -i ./run-election.sh 20
   ```

   **Run 50, on the team box, inside tmux:**

   ```
   cd ~/kasukabe && git pull
   cd app && CONFIRM_KEY_ROTATED=1 OUT=~/results/election50 ./run-election.sh 50
   ```

   The box's 4-hour limit will cut the session at least once. Start it again and
   run the same command with `RESUME=1`. The checkpoints, the engine homes and the video
   frames all carry on.
3. **Watch the viewer.** At the end, the report opens with the scored checklist.

## What could still go differently

- **Nobody stands, or few vote.** The rules are stated, not urged.
- **A seat does nothing on day 3.** Holding power and using it are different
  findings.
- **The team box's shared budget** (about 10.5 hours in total) runs short. The
  50-resident run needs about 7 of those hours, or 5½ to tick 480. Check the start
  page before starting, and tell the team.
- **Cost runs high.** Each cap sits about $60–100 above the top of its estimate.
